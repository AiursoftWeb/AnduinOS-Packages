"""Recovery preparation eligibility and approval boundaries; no host disk writes."""
from contextlib import nullcontext
import copy
import unittest
from unittest.mock import patch

from anduinos_driver_center import recovery_partition as partition

MIB = partition.MIB
ROOT_UUID = '11111111-1111-4111-8111-111111111111'
SWAP_UUID = '22222222-2222-4222-8222-222222222222'


def layout(swap_bytes=512 * MIB, sector=512):
    disk_bytes = 4 * 1024 * MIB
    start = MIB // sector
    swap_start = start + 128 * MIB // sector
    root_start = swap_start + swap_bytes // sector
    root_size = (disk_bytes - MIB) // sector - root_start
    table = dict(label='gpt', id='33333333-3333-4333-8333-333333333333', device='/dev/vda',
                 unit='sectors', firstlba=start, lastlba=disk_bytes // sector - 34, sectorsize=sector,
                 partitions=[
                     dict(node='/dev/vda1', start=start, size=128 * MIB // sector,
                          type='c12a7328-f81f-11d2-ba4b-00a0c93ec93b', uuid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
                     dict(node='/dev/vda2', start=swap_start, size=swap_bytes // sector,
                          type=partition.SWAP_TYPE, uuid='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
                     dict(node='/dev/vda3', start=root_start, size=root_size,
                          type=partition.LINUX_TYPE, uuid='cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
                 ])
    root = dict(fstype='btrfs', fsroot='/@root', uuid=ROOT_UUID)
    blocks = [dict(name='/dev/vda', type='disk', size=disk_bytes, **{'maj:min': '252:0'}),
              dict(name='/dev/vda2', pkname='/dev/vda', type='part', size=swap_bytes, fstype='swap',
                   uuid=SWAP_UUID, label='AnduinOS-swap', **{'maj:min': '252:2'}),
              dict(name='/dev/vda3', pkname='/dev/vda', type='part', fstype='btrfs', uuid=ROOT_UUID)]
    identity = dict(TYPE='swap', UUID=SWAP_UUID, LABEL='AnduinOS-swap')
    swaps = [dict(name='/dev/vda2', type='partition', prio=10, used=0)]
    return dict(root=root, blocks=blocks, table=table, identity=identity, swaps=swaps,
                available_memory=1024 * MIB)


class PreparationPlanTests(unittest.TestCase):
    def test_swap_size_boundary_on_both_sector_sizes(self):
        for sector in (512, 4096):
            for size, reason in ((512 * MIB - sector, 'small-swap'),
                                 (512 * MIB, 'ready'), (513 * MIB, 'ready')):
                with self.subTest(sector=sector, size=size):
                    result = partition.make_plan(**layout(size, sector))
                    self.assertEqual(result['reason'], reason)
                    if reason == 'ready':
                        plan = result['plan']
                        self.assertEqual(plan['remaining_bytes'], size - 256 * MIB)
                        self.assertEqual(plan['new_device'], '/dev/vda4')
                        self.assertEqual(plan['new_start'] * sector,
                                         plan['swap_start'] * sector + plan['remaining_bytes'])
                        self.assertEqual(plan['priority'], 10)

    def test_no_swap_includes_zram_and_swapfile_only_systems(self):
        data = layout()
        data['blocks'] = [b for b in data['blocks'] if b.get('fstype') != 'swap']
        data['blocks'].append(dict(name='/dev/zram0', type='disk', fstype='swap', size=1024 * MIB))
        data['swaps'] = [dict(name='/swapfile', type='file', prio=-2, used=0)]
        self.assertEqual(partition.make_plan(**data)['reason'], 'no-swap')

    def test_multiple_swaps_or_recovery_volumes_do_not_select_an_arbitrary_target(self):
        data = layout()
        data['blocks'].append(dict(data['blocks'][1], name='/dev/vda5'))
        self.assertEqual(partition.make_plan(**data)['reason'], 'multiple-swaps')
        data = layout()
        data['blocks'].append(dict(name='/dev/vdb1', type='part', fstype='ext4', label='AOS_RECOVERY'))
        self.assertEqual(partition.make_plan(**data)['reason'], 'existing-volume')

    def test_live_mapped_and_multi_device_roots_are_rejected(self):
        data = layout()
        data['root']['fsroot'] = '/'
        self.assertEqual(partition.make_plan(**data)['reason'], 'unsupported-root')
        data = layout()
        data['blocks'][0]['type'] = 'crypt'
        self.assertEqual(partition.make_plan(**data)['reason'], 'unsupported-root')
        data = layout()
        data['blocks'].append(dict(data['blocks'][2], name='/dev/vdb2'))
        self.assertEqual(partition.make_plan(**data)['reason'], 'unsupported-root')

    def test_mbr_overlap_misalignment_and_wrong_swap_identity_are_rejected(self):
        for change in ('mbr', 'overlap', 'alignment', 'identity', 'size'):
            with self.subTest(change=change):
                data = layout()
                if change == 'mbr': data['table']['label'] = 'dos'
                if change == 'overlap': data['table']['partitions'][2]['start'] -= 1
                if change == 'alignment': data['table']['partitions'][1]['start'] += 1
                if change == 'identity': data['identity']['UUID'] = ROOT_UUID
                if change == 'size': data['blocks'][1]['size'] += 512
                self.assertNotEqual(partition.make_plan(**data)['reason'], 'ready')

    def test_swapoff_memory_and_priority_must_be_safe(self):
        data = layout()
        data['swaps'][0]['used'] = 1000 * MIB
        self.assertEqual(partition.make_plan(**data)['reason'], 'memory')
        data = layout()
        data['swaps'][0]['prio'] = -2
        self.assertEqual(partition.make_plan(**data)['reason'], 'swap-priority')
        data['swaps'] = []
        result = partition.make_plan(**data)
        self.assertEqual(result['reason'], 'ready')
        self.assertFalse(result['plan']['active'])
        self.assertIsNone(result['plan']['priority'])

    def test_replacement_table_changes_only_swap_size_and_adds_the_recovery_partition(self):
        data = layout(1024 * MIB)
        before = copy.deepcopy(data['table'])
        plan = partition.make_plan(**data)['plan']
        dump = '\n'.join(f"{p['node']} : start= {p['start']}, size= {p['size']}, type={p['type']}, uuid={p['uuid']}"
                         for p in before['partitions'])
        after, text = partition.changed_table(before, plan, dump)
        self.assertEqual(data['table'], before)
        self.assertEqual(after['partitions'][0], before['partitions'][0])
        self.assertEqual(after['partitions'][2], before['partitions'][2])
        swap = after['partitions'][1]
        for key in ('start', 'type', 'uuid', 'node'):
            self.assertEqual(swap[key], before['partitions'][1][key])
        self.assertEqual(swap['size'], 768 * MIB // 512)
        self.assertEqual(after['partitions'][3]['size'] * 512, 256 * MIB)
        self.assertIn('AnduinOS Recovery', text)

    def test_stale_or_caller_selected_plan_cannot_reach_disk_commands(self):
        current = partition.make_plan(**layout())
        for approved in ({'disk': '/dev/sda'}, dict(current['plan'], remaining_bytes=1), None):
            with self.subTest(approved=approved), patch.object(partition, 'operation_lock', return_value=nullcontext()), \
                 patch.object(partition, 'verify_disk') as verify:
                with self.assertRaisesRegex(RuntimeError, 'plan changed'):
                    partition.prepare(approved, inspector=lambda: current)
                verify.assert_not_called()
