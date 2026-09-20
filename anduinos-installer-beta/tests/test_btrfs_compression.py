import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan, TEST_INVENTORY_DIGEST, TEST_TOPOLOGY_DIGEST
from installer_core.btrfs import BtrfsCompression
from installer_core.model import InstallPlan
from installer_core.planning import build_plan
from installer_core.probe import PlatformProbe
from installer_core.storage_inventory import DiskTopologyBinding
from installer_core.steps import InstallContext
from installer_core.storage_steps import MountTargetStep
from installer_core.target_config import ConfigureStorageStep
from installer_core.validation import validate_plan, PlanValidationError


class BtrfsCompressionTests(unittest.TestCase):
    def test_choices_reach_mounts_and_fstab_through_serialized_plan(self):
        base = valid_plan()
        options = {'none': 'compress=no', 'fast': 'compress=zstd:1',
                   'balanced': 'compress=zstd:3', 'space': 'compress=zstd:6'}
        for preset, option in options.items():
            with self.subTest(preset=preset), tempfile.TemporaryDirectory() as directory:
                choices = dict(locale=base.regional.locale, timezone=base.regional.timezone,
                               keyboard=base.regional.keyboard.layout, hostname=base.identity.hostname,
                               username=base.identity.username, full_name=base.identity.full_name,
                               btrfs_compression=preset)
                plan = build_plan(
                    choices, base.storage.disk,
                    PlatformProbe(base.platform.architecture, base.platform.firmware, base.platform.secure_boot),
                    base.identity.password_hash,
                    disk_binding=DiskTopologyBinding(base.storage.disk.stable_id,
                                                     base.storage.disk.expected_size_bytes,
                                                     TEST_TOPOLOGY_DIGEST),
                    inventory_digest=TEST_INVENTORY_DIGEST,
                    physical_memory_probe=lambda: 8 * 1024**3,
                )
                plan = InstallPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
                validate_plan(plan)
                self.assertEqual(plan.storage.btrfs_compression.value, preset)
                runner = FakeRunner()
                devices = {'root': '/dev/sda4', 'efi-system': '/dev/sda2', 'swap': '/dev/sda3'}
                for name, device in devices.items():
                    runner.outputs[('blkid', '-s', 'UUID', '-o', 'value', device)] = (name + '-uuid\n', '', 0)
                context = InstallContext(plan, lambda message: None, values={'partition_devices': devices})
                target = Path(directory) / 'target'
                MountTargetStep(runner, target=target).execute(context)
                ConfigureStorageStep(runner).execute(context)
                fstab = (target / 'etc/fstab').read_text()
                mounts = [command for command, *_ in runner.commands
                          if command[:2] == ('mount', '-o') and 'subvol=' in command[2]]
                self.assertEqual(len(mounts), 6)
                for command in mounts:
                    self.assertIn(option, command[2].split(','))
                entries = [line for line in fstab.splitlines() if ' btrfs ' in line]
                self.assertEqual(len(entries), 6)
                for entry in entries:
                    self.assertIn(option, entry.split()[3].split(','))

    def test_untrusted_compression_is_rejected(self):
        base = valid_plan()
        for value in ('zstd:99', 'fast,noexec', None, 3):
            with self.subTest(value=value):
                raw = base.to_dict()
                raw['storage']['btrfs_compression'] = value
                with self.assertRaises((ValueError, TypeError)):
                    InstallPlan.from_dict(raw)
                invalid = replace(base, storage=replace(base.storage, btrfs_compression=value))
                with self.assertRaises(PlanValidationError):
                    validate_plan(invalid)

    def test_default_is_balanced(self):
        self.assertIs(valid_plan().storage.btrfs_compression, BtrfsCompression.BALANCED)
