"""Storage and credential behavior; native cryptsetup is also qualified in VMs."""

import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.encryption import (
    EncryptionCredentials, MACHINE_KEY_PATH, close_encrypted_volumes,
    generate_recovery_key, preflight_encryption, prepare_encrypted_volume,
    read_encryption_credentials,
)
from installer_core.esp import NvramInspection
from installer_core.executor import InstallerExecutor
from installer_core.model import (
    Architecture, EncryptionMode, Filesystem, Firmware, InstallMode,
    InstallPlan, SecureBoot, SwapEncryption,
)
from installer_core.steps import InstallContext, StepRunner
from installer_core.storage_graph_planning import build_erase_disk_storage_graph
from installer_core.storage_inventory import DiskTopologyBinding
from installer_core.storage_planning import build_erase_disk_execution_plan
from installer_core.storage_steps import PrepareStorageStep
from installer_core.storage_write_set import StorageAction
from installer_core.target_config import (
    ConfigureStorageStep, ENCRYPTED_SLEEP_PATH, ENCRYPTED_SLEEP_POLICY,
)
from installer_core.validation import ExecutionPolicy, PlanValidationError, validate_plan


PASSPHRASE = "disposable test password"
RECOVERY = "abcdef0123456789" * 3
LUKS_UUID = "f2b6c8d0-1234-4321-9876-112233445566"
SWAP_PARTUUID = "e3c6d8f0-1234-4321-9876-112233445566"


def rebuild_graph(plan):
    reference = plan.storage.graph.block_references[0]
    graph = build_erase_disk_storage_graph(
        plan,
        DiskTopologyBinding(reference.stable_id, reference.expected_size_bytes,
                            reference.topology_digest),
        plan.storage.graph.inventory_digest,
    )
    return replace(plan, storage=replace(plan.storage, graph=graph))


def encrypted_plan(**kwargs):
    base = valid_plan(**kwargs)
    return rebuild_graph(replace(
        base, storage=replace(base.storage, encryption=EncryptionMode.LUKS2),
    ))


class RecordingCryptsetup(FakeRunner):
    """Observe inherited key bytes while their anonymous descriptors are open."""

    def __init__(self):
        super().__init__()
        self.new_keys = []

    def run(self, command, **kwargs):
        if tuple(command[:2]) == ("cryptsetup", "luksAddKey"):
            descriptor, = kwargs["pass_fds"]
            self.new_keys.append(os.pread(descriptor, 4096, 0))
        return super().run(command, **kwargs)


class EncryptedStorageTests(unittest.TestCase):
    def test_layers_and_write_authorization_cover_filesystems_and_architectures(self):
        for arch in Architecture:
            for filesystem in (Filesystem.BTRFS, Filesystem.EXT4):
                for swap in (SwapEncryption.AUTO, SwapEncryption.PLAIN):
                    with self.subTest(arch=arch, filesystem=filesystem, swap=swap):
                        plan = encrypted_plan(architecture=arch, filesystem=filesystem)
                        plan = rebuild_graph(replace(plan, swap=replace(plan.swap, encryption=swap)))
                        validate_plan(plan)
                        execution = build_erase_disk_execution_plan(plan)
                        commands = execution.commands
                        self.assertNotEqual(commands.devices["root"], commands.filesystem_devices["root"])
                        self.assertEqual(
                            commands.filesystem_devices["root"],
                            "/dev/mapper/anduinos-install-root",
                        )
                        self.assertNotIn(commands.devices["root"], [c[-1] for c in commands.format])
                        self.assertEqual(
                            [(x.action.value, x.target_id) for x in plan.storage.graph.operations],
                            [(x.action.value, x.target_id) for x in execution.write_set.operations],
                        )
                        self.assertEqual(
                            len(plan.storage.graph.encrypted_volumes),
                            2 if swap is SwapEncryption.AUTO else 1,
                        )
                        self.assertNotIn(
                            StorageAction.WRITE_BIOS_BOOTLOADER,
                            [x.action for x in execution.write_set.operations],
                        )
                        self.assertEqual(bool(plan.storage.graph.subvolumes), filesystem is Filesystem.BTRFS)

    def test_plain_reinstall_clears_only_the_declared_format_targets(self):
        plan = valid_plan()
        execution = build_erase_disk_execution_plan(plan)
        context = InstallContext(plan, lambda _: None, values={
            "storage_execution_plan": execution,
        })
        runner = FakeRunner()
        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            PrepareStorageStep(runner).execute(context)
        cleared = [command[-1] for command, _ in runner.commands
                   if command[0] == "wipefs"]
        self.assertEqual(cleared, [command[-1] for command in execution.commands.format])
        self.assertNotIn(plan.storage.disk.path, cleared)

    def test_plan_roundtrip_contains_policy_but_no_credentials(self):
        plan = encrypted_plan()
        serialized = json.dumps(plan.to_dict())
        self.assertEqual(InstallPlan.from_dict(json.loads(serialized)), plan)
        for name in ("passphrase", "recovery_key", "machine.key"):
            self.assertNotIn(name, serialized)

    def test_plain_default_and_zero_swap_have_no_extra_volume(self):
        ordinary = build_erase_disk_execution_plan(valid_plan())
        self.assertFalse(ordinary.commands.encrypted_volumes)
        self.assertEqual(ordinary.commands.devices, ordinary.commands.filesystem_devices)
        self.assertFalse(valid_plan().encrypted_swap)
        encrypted = encrypted_plan(swap_size_mib=0)
        self.assertFalse(encrypted.encrypted_swap)
        self.assertEqual(len(encrypted.storage.graph.encrypted_volumes), 1)

    def test_release_refuses_encryption_before_any_commands(self):
        runner = FakeRunner()
        credentials = EncryptionCredentials(PASSPHRASE, RECOVERY)
        with self.assertRaisesRegex(PlanValidationError, "not enabled"):
            InstallerExecutor(lambda _: None, runner=runner).run(
                encrypted_plan(), encryption_credentials=credentials,
            )
        self.assertEqual(runner.commands, [])
        self.assertEqual(credentials.passphrase, "")

    def test_encryption_is_limited_to_new_uefi_installations(self):
        plan = encrypted_plan()
        for mode in (InstallMode.MANUAL, InstallMode.GUIDED_COEXISTENCE):
            invalid = replace(plan, storage=replace(plan.storage, mode=mode))
            with self.assertRaisesRegex(PlanValidationError, "whole-disk UEFI"):
                validate_plan(invalid)
        invalid = replace(plan, platform=replace(plan.platform, firmware=Firmware.BIOS,
                                                secure_boot=SecureBoot.NOT_APPLICABLE))
        with self.assertRaisesRegex(PlanValidationError, "whole-disk UEFI"):
            validate_plan(invalid)

    def test_missing_credentials_fail_before_disk_writes(self):
        plan = encrypted_plan()
        runner = FakeRunner()
        context = InstallContext(plan, lambda _: None,
                                 execution_policy=ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST)
        step = PrepareStorageStep(runner, nvram_inspector=lambda _: NvramInspection(True))
        result = StepRunner([step]).run(context)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.destructive_started)
        self.assertIn("credentials are required", result.results[0].message)
        self.assertFalse(any(c[0] in {"parted", "cryptsetup"} for c, _ in runner.commands))

    def test_existing_mapping_is_not_reused_or_closed(self):
        plan = encrypted_plan()
        volumes = build_erase_disk_execution_plan(plan).commands.encrypted_volumes
        context = InstallContext(plan, lambda _: None,
                                 encryption_credentials=EncryptionCredentials(PASSPHRASE, RECOVERY))
        runner = FakeRunner()
        with patch("installer_core.encryption.os.path.lexists", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                preflight_encryption(context, runner, volumes)
        close_encrypted_volumes(context, runner, strict=True)
        self.assertEqual(runner.commands, [])

    def test_recovery_key_verified_before_open_without_secret_arguments(self):
        plan = encrypted_plan()
        volume = build_erase_disk_execution_plan(plan).commands.encrypted_volumes[0]
        context = InstallContext(plan, lambda _: None,
                                 encryption_credentials=EncryptionCredentials(PASSPHRASE, RECOVERY))
        runner = RecordingCryptsetup()
        prepare_encrypted_volume(context, runner, volume)
        self.assertEqual(runner.new_keys, [RECOVERY.encode("ascii")])
        self.assertEqual([c[1] for c, _ in runner.commands], ["luksFormat", "luksAddKey", "open", "open"])
        for command, options in runner.commands:
            self.assertNotIn(PASSPHRASE, " ".join(command))
            self.assertNotIn(RECOVERY, " ".join(command))
            self.assertFalse(options.get("log_output", True))
        close_encrypted_volumes(context, runner, strict=True)
        self.assertFalse(context.values["opened_encrypted_volumes"])
        self.assertEqual(runner.commands[-1][0], ("cryptsetup", "close", "anduinos-install-root"))

    def test_target_config_distinguishes_luks_filesystem_and_random_swap(self):
        for filesystem in (Filesystem.BTRFS, Filesystem.EXT4):
            for policy in (SwapEncryption.AUTO, SwapEncryption.PLAIN):
                plan = encrypted_plan(filesystem=filesystem)
                plan = rebuild_graph(replace(plan, swap=replace(plan.swap, encryption=policy)))
                commands = build_erase_disk_execution_plan(plan).commands
                runner = RecordingCryptsetup()
                credentials = EncryptionCredentials(PASSPHRASE, RECOVERY)
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory)
                    context = InstallContext(plan, lambda _: None, values={
                        "target": target, "partition_devices": commands.devices,
                        "filesystem_devices": commands.filesystem_devices,
                        "opened_encrypted_volumes": list(commands.encrypted_volumes),
                    }, encryption_credentials=credentials)
                    for name, device in commands.filesystem_devices.items():
                        command = ("blkid", "-s", "UUID", "-o", "value", device)
                        runner.outputs[command] = (name + "-inner-uuid", "", 0)
                    runner.outputs[("cryptsetup", "luksUUID", commands.devices["root"])] = (LUKS_UUID, "", 0)
                    swap_probe = (
                        "blkid", "-s", "PARTUUID", "-o", "value", commands.devices["swap"],
                    )
                    runner.outputs[swap_probe] = (SWAP_PARTUUID, "", 0)
                    step = ConfigureStorageStep(runner)
                    step.execute(context)
                    step.verify(context)
                    fstab = (target / "etc/fstab").read_text()
                    crypttab = (target / "etc/crypttab").read_text()
                    key = target / MACHINE_KEY_PATH
                    self.assertEqual(key.read_bytes(), runner.new_keys[0])
                    self.assertEqual((key.stat().st_size, key.stat().st_mode & 0o777), (64, 0o600))
                    self.assertIn("UUID=root-inner-uuid / ", fstab)
                    self.assertNotIn(LUKS_UUID, fstab)
                    self.assertIn(f"anduinos-root UUID={LUKS_UUID} none luks,x-initrd.attach", crypttab)
                    self.assertNotIn("machine.key", crypttab)
                    if plan.encrypted_swap:
                        self.assertIn(f"PARTUUID={SWAP_PARTUUID} /dev/urandom swap,", crypttab)
                        self.assertIn("/dev/mapper/anduinos-swap none swap", fstab)
                        self.assertNotIn("swap-inner-uuid", fstab)
                    else:
                        self.assertNotIn("anduinos-swap", crypttab)
                        self.assertIn("UUID=swap-inner-uuid none swap", fstab)
                    self.assertEqual("subvol=@root" in fstab, filesystem is Filesystem.BTRFS)
                    self.assertEqual(
                        (target / ENCRYPTED_SLEEP_PATH).read_text(), ENCRYPTED_SLEEP_POLICY,
                    )
                    self.assertEqual(credentials.passphrase, "")
                    self.assertIsNone(context.encryption_credentials)

    def test_private_line_is_bounded_without_echoing_invalid_content(self):
        credentials = read_encryption_credentials(io.StringIO(json.dumps({
            "passphrase": PASSPHRASE, "recovery_key": RECOVERY,
        }) + "\n"))
        self.assertEqual(credentials.passphrase, PASSPHRASE)
        self.assertNotIn(PASSPHRASE, repr(credentials))
        for line in ("", "x" * 4097, '{"passphrase": "not complete"}'):
            with self.assertRaisesRegex(ValueError, "Invalid or missing") as error:
                read_encryption_credentials(io.StringIO(line))
            self.assertNotIn("not complete", str(error.exception))
        self.assertRegex(generate_recovery_key(), r"^[a-f0-9]{48}$")


if __name__ == "__main__":
    unittest.main()
