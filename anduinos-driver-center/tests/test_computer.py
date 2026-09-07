from pathlib import Path
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from anduinos_driver_center import computer

loader = importlib.machinery.SourceFileLoader('memory_helper', str(ROOT / 'scripts/memory-helper'))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class ComputerTests(unittest.TestCase):
    def test_branding_uses_identity_not_ancestry_or_description(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lsb-release').write_text('DISTRIB_ID=AnduinOS\nDISTRIB_DESCRIPTION="AnduinOS 2"')
            self.assertEqual(computer.distribution(root), ('AnduinOS 2', True))
            (root / 'os-release').write_text('ID=ubuntu\nID_LIKE=anduinos\nPRETTY_NAME="AnduinOS themed Ubuntu"')
            self.assertEqual(computer.distribution(root), ('AnduinOS themed Ubuntu', False))
            (root / 'os-release').write_text('ID=anduinos\nPRETTY_NAME="AnduinOS 2.0.2"')
            self.assertEqual(computer.distribution(root), ('AnduinOS 2.0.2', True))

    def test_system_disk_traces_raid_and_encryption_and_ignores_ram(self):
        root = {'name': 'root', 'type': 'crypt', 'mountpoints': ['/home', '/']}
        raid = {'name': 'md0', 'type': 'raid1', 'children': [root]}
        devices = [dict(name=name, type='disk', size=512_000_000_000, children=[raid]) for name in ('sda', 'sdb')]
        devices += [{'name': 'nvme0n1', 'type': 'disk', 'size': 1_000_000_000_000},
                    {'name': 'zram0', 'type': 'disk', 'size': 32_000_000_000}]
        disks = computer.disks_from_json({'blockdevices': devices})
        self.assertEqual([d.name for d in disks if d.system], ['sda', 'sdb'])
        self.assertEqual([d.name for d in disks], ['sda', 'sdb', 'nvme0n1'])
        self.assertNotIn('/home', str(disks))

    def test_btrfs_multiple_mounts_are_system_disk(self):
        disks = computer.disks_from_json({'blockdevices': [{'name': 'nvme0n1', 'type': 'disk', 'children': [
            {'name': 'p1', 'type': 'part', 'fstype': 'btrfs', 'mountpoints': ['/var/log', '/', '/home']}]}]})
        self.assertTrue(disks[0].system)

    def test_missing_commands_do_not_require_privileges(self):
        with patch.object(computer.subprocess, 'run', side_effect=FileNotFoundError) as run:
            info = computer.scan_computer()
        self.assertEqual(info.disks, [])
        self.assertEqual(info.pci['graphics'], [])
        self.assertTrue(all(call.args[0][0] not in {'sudo', 'pkexec'} for call in run.call_args_list))

    def test_command_timeout_is_partial_information(self):
        with patch.object(computer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('lscpu', 12)):
            self.assertEqual(computer.command('lscpu'), '')
        self.assertEqual(computer.json_object('not json'), {})

    def test_pci_drivers_are_associated_with_correct_devices(self):
        data = computer.pci_devices('Slot:\t01:00.0\nClass:\tVGA compatible controller\nVendor:\tNVIDIA\nDevice:\tRTX\nDriver:\tnvidia\n\nSlot:\t05:00.0\nClass:\tNetwork controller\nDevice:\tAX210\nDriver:\tiwlwifi')
        self.assertEqual(data['graphics'][0]['driver'], 'nvidia')
        self.assertEqual(data['network'][0]['driver'], 'iwlwifi')

    def test_edid_validates_checksum_and_omits_serial(self):
        data = bytearray(128)
        data[:8] = b'\x00\xff\xff\xff\xff\xff\xff\x00'
        data[21:23] = bytes([60, 34])
        data[54:72] = b'\x00\x00\x00\xfc\x00Test Monitor\n'
        data[127] = (-sum(data[:127])) % 256
        parsed = computer.edid_info(bytes(data))
        self.assertEqual(parsed['model'], 'Test Monitor')
        self.assertIn('″', parsed['size'])
        data[10] ^= 1
        self.assertEqual(computer.edid_info(bytes(data)), {})
        self.assertEqual(computer.edid_info(b''), {})

    def test_memory_usage_excludes_available_cache_and_handles_no_swap(self):
        usage = computer.memory_usage('MemTotal:       1000 kB\nMemAvailable:    600 kB\nSwapTotal:         0 kB\nSwapFree:          0 kB')
        self.assertEqual(usage['memory_used'], 400 * 1024)
        self.assertEqual(usage['swap_used'], 0)
        self.assertEqual(usage['swap_total'], 0)
        self.assertIsNone(computer.memory_usage('MemTotal: 1000 kB')['memory_used'])

    def test_package_counts_ignore_uninstalled_and_preserve_installation_scope(self):
        with patch.object(computer, 'command_result', side_effect=[
            'installed\nconfig-files\nunpacked\ninstalled\n',
            'app/example/x86_64/stable\tsystem\napp/example/x86_64/stable\tuser\napp/example/x86_64/stable\tuser\n',
        ]):
            self.assertEqual(computer.package_counts(), (2, 2))
        with patch.object(computer, 'command_result', side_effect=['', None]):
            self.assertEqual(computer.package_counts(), (0, None))

    def test_filesystem_usage_deduplicates_subvolumes_and_uses_available_blocks(self):
        mounts = [
            {'source': '/dev/nvme0n1p1[/@home]', 'target': '/home', 'fstype': 'btrfs', 'maj:min': '0:35'},
            {'source': '/dev/nvme0n1p1[/@]', 'target': '/', 'fstype': 'btrfs', 'maj:min': '0:35'},
            {'source': '/dev/md0', 'target': '/mnt/data', 'fstype': 'ext4', 'maj:min': '9:0'},
            {'source': '/dev/loop0', 'target': '/snap/example', 'fstype': 'squashfs', 'maj:min': '7:0'},
            {'source': 'server:/share', 'target': '/mnt/network', 'fstype': 'nfs'},
            {'source': 'tmpfs', 'target': '/run', 'fstype': 'tmpfs'},
        ]
        stat = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=30, f_frsize=1024)
        with patch.object(computer.os, 'statvfs', return_value=stat) as probe:
            volumes = computer.filesystem_usage({'filesystems': mounts})
        self.assertEqual([v.target for v in volumes], ['/', '/mnt/data'])
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(volumes[0].used, 60 * 1024)
        self.assertEqual(volumes[0].available, 30 * 1024)
        self.assertEqual(volumes[0].source, '/dev/nvme0n1p1')

    def test_filesystem_usage_skips_unreadable_mounts_and_redacts_usernames(self):
        mounts = [
            {'source': '/dev/sda', 'target': '/media/alice/Backup', 'fstype': 'ext4'},
            {'source': '/dev/sdb', 'target': '/unreadable', 'fstype': 'ext4'},
        ]
        stat = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=30, f_frsize=1024)
        with patch.object(computer.os, 'statvfs', side_effect=[stat, PermissionError()]):
            volumes = computer.filesystem_usage({'filesystems': mounts})
        self.assertEqual(len(volumes), 1)
        self.assertNotIn('alice', str(volumes))

    def test_running_gnome_version_is_used_and_failed_probe_does_not_guess_mutter(self):
        with patch.dict(computer.os.environ, {'XDG_CURRENT_DESKTOP': 'GNOME', 'XDG_SESSION_TYPE': 'wayland'}), \
             patch.object(computer, 'command', return_value="(<'50.1'>,)"), \
             patch.object(computer, 'package_counts', return_value=(2, 0)), \
             patch.object(computer, 'appearance_settings', return_value={}):
            info = computer.scan_details()
        self.assertEqual(info.desktop, 'GNOME 50.1')
        self.assertEqual(info.window_manager, 'Mutter')
        with patch.dict(computer.os.environ, {'XDG_CURRENT_DESKTOP': 'GNOME'}), \
             patch.object(computer, 'command', return_value=''), \
             patch.object(computer, 'package_counts', return_value=(None, None)), \
             patch.object(computer, 'appearance_settings', return_value={}):
            self.assertEqual(computer.scan_details().window_manager, '')

    def test_memory_helper_filters_empty_slots_and_identifiers(self):
        text = '''Handle 0x0001, DMI type 17, 92 bytes
Memory Device
\tSize: No Module Installed
\tSerial Number: SECRET

Handle 0x0002, DMI type 17, 92 bytes
Memory Device
\tSize: 16 GB
\tLocator: DIMM_A2
\tType: DDR5
\tSpeed: 7200 MT/s
\tConfigured Memory Speed: 6000 MT/s
\tSerial Number: SECRET
\tAsset Tag: PRIVATE
\tManufacturer: Example
'''
        modules = helper.parse_memory(text)
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0]['Configured Memory Speed'], '6000 MT/s')
        self.assertEqual(modules[0]['Type'], 'DDR5')
        self.assertNotIn('SECRET', json.dumps(modules))
        self.assertNotIn('PRIVATE', json.dumps(modules))

    def test_memory_helper_accepts_no_arguments_and_uses_fixed_command(self):
        with patch.object(helper.os, 'geteuid', return_value=0), patch.object(helper.subprocess, 'run') as run:
            self.assertEqual(helper.main(['--type', 'system']), 64)
            run.assert_not_called()
            run.return_value.stdout = ''
            self.assertEqual(helper.main([]), 0)
            self.assertEqual(run.call_args.args[0], ['/usr/sbin/dmidecode', '--type', '17'])
            self.assertFalse(run.call_args.kwargs.get('shell', False))
        with patch.object(helper.os, 'geteuid', return_value=1000):
            self.assertEqual(helper.main([]), 77)


if __name__ == '__main__':
    unittest.main()
