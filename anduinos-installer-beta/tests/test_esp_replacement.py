"""Explicit replacement authorization and recoverable, vendor-only writes."""

import contextlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import subprocess

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.esp import (
    EspReuseInspection, EspTreeEntry, NvramInspection, capture_esp_vendor_tree,
    esp_vendor_digest,
)
from installer_core.esp_replacement import backup_esp_replacement, replace_esp_vendor
from installer_core.model import EspReplacement, InstallMode, InstallPlan
from installer_core.steps import InstallContext
from installer_core.storage_graph_planning import build_guided_coexistence_storage_graph
from installer_core.storage_planning import (
    build_guided_coexistence_execution_plan, build_manual_storage_execution_plan,
)
from installer_core.storage_steps import PrepareStorageStep
from installer_core.validation import validate_plan, PlanValidationError
from test_guided_storage_graph import guided_plan
from test_manual_layout import manual_disk, selection
from test_manual_storage_graph import manual_plan


PARTUUID = '11111111-2222-3333-4444-555555555555'
FSUUID = 'ABCD-1234'


def replacement_plan(manual=True, new_esp=False):
    if manual:
        disk = manual_disk()
    else:
        base, inventory = guided_plan(reuse_esp=not new_esp)
        disk = inventory.disks[0]
    esp = replace(disk.partitions[0],
                  identity=replace(disk.partitions[0].identity, partuuid=PARTUUID),
                  filesystem_uuid=FSUUID)
    disk = replace(disk, partitions=(esp, *disk.partitions[1:]))
    if manual:
        plan, inventory = manual_plan(disk=disk, chosen=selection(reused_esp=PARTUUID))
    else:
        graph = build_guided_coexistence_storage_graph(
            replace(base, storage=replace(base.storage, graph=None)), disk,
            disk.free_extents[0], inventory_digest=inventory.digest,
            reused_esp=None if new_esp else esp)
        plan = replace(base, storage=replace(base.storage, graph=graph))
        inventory = replace(inventory, disks=(disk,))
    entries = (EspTreeEntry('EFI/AnduinOS', 'directory', 0),)
    authorization = EspReplacement(PARTUUID, FSUUID, esp_vendor_digest(entries))
    return replace(plan, boot=replace(plan.boot, esp_replacement=authorization)), inventory


class EspReplacementPlanTests(unittest.TestCase):
    def test_strict_round_trip_and_nested_unknown_field(self):
        plan, _inventory = replacement_plan()
        validate_plan(plan)
        self.assertEqual(InstallPlan.from_dict(plan.to_dict()), plan)
        value = plan.to_dict()
        value['boot']['esp_replacement']['command'] = 'anything'
        with self.assertRaisesRegex(ValueError, 'Unknown field'):
            InstallPlan.from_dict(value)

    def test_invalid_identity_fingerprint_and_erase_mode_are_rejected(self):
        plan, _inventory = replacement_plan()
        for field, value in [('partuuid', '../other'), ('filesystem_uuid', []),
                             ('vendor_digest', 'not-a-digest')]:
            with self.subTest(field=field), self.assertRaises(PlanValidationError):
                validate_plan(replace(plan, boot=replace(plan.boot, esp_replacement=
                    replace(plan.boot.esp_replacement, **{field: value}))))
        erased = valid_plan()
        with self.assertRaisesRegex(PlanValidationError, 'reused UEFI ESP'):
            validate_plan(replace(erased, boot=replace(erased.boot,
                esp_replacement=plan.boot.esp_replacement)))

    def test_authorization_allows_only_exact_inspected_namespace_in_both_modes(self):
        for manual in (False, True):
            plan, inventory = replacement_plan(manual)
            builder = build_manual_storage_execution_plan if manual else build_guided_coexistence_execution_plan
            inspection = EspReuseInspection(PARTUUID, FSUUID, True, 128 * 1024**2,
                vendor_entries=(EspTreeEntry('EFI/AnduinOS', 'directory', 0),))
            execution = builder(plan, inventory, esp_inspection=inspection,
                                nvram_inspection=NvramInspection(True))
            self.assertTrue(execution.reuses_esp)
            self.assertFalse(any(c[0] == 'mkfs.vfat' for c in execution.commands.format))
            for changed in (replace(inspection, partuuid='other'),
                            replace(inspection, filesystem_uuid='FFFF-FFFF'),
                            replace(inspection, vendor_entries=()),
                            replace(inspection, vendor_entries=inspection.vendor_entries + (
                                EspTreeEntry('EFI/AnduinOS/new', 'file', 1, 'a' * 64),))):
                with self.subTest(manual=manual, changed=changed), self.assertRaises(RuntimeError):
                    builder(plan, inventory, esp_inspection=changed,
                            nvram_inspection=NvramInspection(True))

    def test_authorization_for_new_esp_is_rejected(self):
        plan, inventory = replacement_plan(False, new_esp=True)
        with self.assertRaisesRegex(RuntimeError, 'reused ESP'):
            build_guided_coexistence_execution_plan(plan, inventory,
                esp_inspection=None, nvram_inspection=NvramInspection(True))

    def test_backup_failure_precedes_all_partition_writes(self):
        plan, inventory = replacement_plan()
        runner = FakeRunner()
        step = PrepareStorageStep(runner, inventory_probe=lambda: inventory)
        context = InstallContext(plan, lambda _: None)
        # Compile a valid execution first; backup failure must not call parted.
        inspection = EspReuseInspection(PARTUUID, FSUUID, True, 128 * 1024**2,
            vendor_entries=(EspTreeEntry('EFI/AnduinOS', 'directory', 0),))
        step.esp_inspector = lambda *_: inspection
        step.nvram_inspector = lambda *_: NvramInspection(True)
        step.preflight(context)
        with patch('installer_core.storage_steps.backup_esp_replacement',
                   side_effect=OSError('backup disk full')):
            with self.assertRaisesRegex(OSError, 'backup disk full'):
                step.execute(context)
        self.assertFalse(any(c[0] in {'parted', 'mkfs.vfat', 'mkfs.btrfs', 'swapoff'}
                             for c, _ in runner.commands))


class EspReplacementBackupTests(unittest.TestCase):
    def fixture(self, root, namespace='EFI/AnduinOS'):
        (root / namespace).mkdir(parents=True)
        (root / namespace / 'shimx64.efi').write_bytes(b'old-loader')
        prefix = namespace.split('/')[0]
        for path in (f'{prefix}/Microsoft/Boot/bootmgfw.efi', f'{prefix}/ubuntu/shimx64.efi',
                     f'{prefix}/BOOT/BOOTX64.EFI'):
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'do-not-touch')

    def identity_runner(self):
        return SimpleNamespace(run=Mock(return_value=subprocess.CompletedProcess(
            [], 0, f'PARTUUID={PARTUUID}\nUUID={FSUUID}\n', '')))

    def test_changed_source_fails_backup_and_always_unmounts(self):
        plan, inventory = replacement_plan()
        owner = self
        class Runner(FakeRunner):
            def run(inner, command, **kwargs):
                if command[0] == 'mount':
                    owner.fixture(Path(command[-1]))
                return super().run(command, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            runner = Runner()
            runner.outputs[('blkid', '-o', 'export', inventory.disks[0].partitions[0].identity.path)] = (
                f'PARTUUID={PARTUUID}\nUUID={FSUUID}\n', '', 0)
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                backup_esp_replacement(inventory.disks[0].partitions[0],
                    plan.boot.esp_replacement, runner, backup_root=Path(tmp))
            self.assertEqual([c[0] for c, _ in runner.commands], ['blkid', 'mount', 'umount'])

    def test_wrong_identity_failed_copy_and_corrupt_archive_preserve_old_files(self):
        for scenario in ('identity', 'copy', 'archive'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / 'target'
                mounted = target / 'boot/efi'
                self.fixture(mounted)
                plan, _ = replacement_plan()
                authorization = replace(plan.boot.esp_replacement,
                    vendor_digest=esp_vendor_digest(capture_esp_vendor_tree(mounted)))
                plan = replace(plan, boot=replace(plan.boot, esp_replacement=authorization))
                backup = Path(tmp) / 'live-backup'
                backup.mkdir()
                with tarfile.open(backup / 'anduinos-efi.tar', 'w') as archive:
                    archive.add(mounted / 'EFI/AnduinOS', arcname='EFI/AnduinOS')
                (backup / 'recovery.txt').write_text('recovery')
                context = InstallContext(plan, lambda _: None)
                context.values.update(esp_replacement_backup=backup,
                    partition_devices={'efi-system': '/dev/vda1'})
                runner = self.identity_runner()
                if scenario == 'identity':
                    runner.run.return_value.stdout = 'PARTUUID=other\nUUID=ABCD-1234\n'
                elif scenario == 'archive':
                    (backup / 'anduinos-efi.tar').write_bytes(b'corrupt backup')
                with patch('installer_core.esp_replacement.shutil.copyfile',
                           side_effect=OSError('disk full')) if scenario == 'copy' else contextlib.nullcontext():
                    with self.assertRaises((RuntimeError, OSError, tarfile.TarError)):
                        replace_esp_vendor(context, target, runner)
                self.assertEqual((mounted / 'EFI/AnduinOS/shimx64.efi').read_bytes(), b'old-loader')
                self.assertEqual((mounted / 'EFI/Microsoft/Boot/bootmgfw.efi').read_bytes(), b'do-not-touch')

    def test_backup_is_verified_and_removal_is_limited_to_authorized_vendor(self):
        for namespace in ('EFI/AnduinOS', 'efi/ANDUINOS'):
            with self.subTest(namespace=namespace), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / 'target'
                mounted = target / 'boot/efi'
                self.fixture(mounted, namespace)
                plan, inventory = replacement_plan()
                authorization = replace(plan.boot.esp_replacement,
                    vendor_digest=esp_vendor_digest(capture_esp_vendor_tree(mounted)))
                plan = replace(plan, boot=replace(plan.boot, esp_replacement=authorization))
                class Runner(FakeRunner):
                    def run(inner, command, **kwargs):
                        if command[0] == 'mount':
                            self.fixture(Path(command[-1]), namespace)
                        return super().run(command, **kwargs)
                backup_runner = Runner()
                backup_runner.outputs[('blkid', '-o', 'export', inventory.disks[0].partitions[0].identity.path)] = (
                    f'PARTUUID={PARTUUID}\nUUID={FSUUID}\n', '', 0)
                backup = backup_esp_replacement(inventory.disks[0].partitions[0], authorization,
                    backup_runner, backup_root=root / 'live-backups')
                context = InstallContext(plan, lambda _: None)
                context.values['esp_replacement_backup'] = backup
                context.values['partition_devices'] = {'efi-system': '/dev/nvme0n1p1'}
                replace_esp_vendor(context, target, self.identity_runner())
                self.assertFalse((mounted / namespace).exists())
                prefix = namespace.split('/')[0]
                for path in (f'{prefix}/Microsoft/Boot/bootmgfw.efi', f'{prefix}/ubuntu/shimx64.efi',
                             f'{prefix}/BOOT/BOOTX64.EFI'):
                    self.assertEqual((mounted / path).read_bytes(), b'do-not-touch')
                saved = list((target / 'var/backups/anduinos-installer').glob('*/anduinos-efi.tar'))
                self.assertEqual(len(saved), 1)
                with tarfile.open(saved[0]) as archive:
                    self.assertEqual(archive.extractfile(namespace + '/shimx64.efi').read(), b'old-loader')
                    self.assertEqual(set(archive.getnames()), {namespace, namespace + '/shimx64.efi'})

    def test_changed_files_and_missing_backup_never_remove_the_old_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            mounted = target / 'boot/efi'
            self.fixture(mounted)
            plan, _ = replacement_plan()
            context = InstallContext(plan, lambda _: None)
            context.values['partition_devices'] = {'efi-system': '/dev/nvme0n1p1'}
            with self.assertRaisesRegex(RuntimeError, 'backup is missing'):
                replace_esp_vendor(context, target, self.identity_runner())
            backup = target / 'old-backup'
            backup.mkdir()
            (backup / 'anduinos-efi.tar').write_bytes(b'placeholder')
            context.values['esp_replacement_backup'] = backup
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                replace_esp_vendor(context, target, self.identity_runner())
            self.assertEqual((mounted / 'EFI/AnduinOS/shimx64.efi').read_bytes(), b'old-loader')


if __name__ == '__main__':
    unittest.main()
