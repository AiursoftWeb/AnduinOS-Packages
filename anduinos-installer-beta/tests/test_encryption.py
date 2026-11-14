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
    generate_recovery_key, install_machine_key, preflight_encryption, prepare_encrypted_volume,
    read_encryption_credentials,
)
from installer_core.esp import NvramInspection
from installer_core.executor import InstallerExecutor
from installer_core.live_cleanup import ENCRYPTED_ROOT_PACKAGES, RemoveLivePackagesStep
from installer_core.boot_commands import build_boot_commands
from installer_core.bootloader import _verify_encrypted_boot_output
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
    ENCRYPTED_GRUB_PATH,
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
        if tuple(command[:3]) == ("cryptsetup", "token", "import"):
            readback = ("cryptsetup", "luksDump", "--dump-json-metadata", command[-1])
            self.outputs.setdefault(readback, (json.dumps({"tokens": {"0": json.loads(kwargs["input_text"])}}), "", 0))
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

    def test_encrypted_boot_commands_never_install_bios(self):
        for architecture in Architecture:
            commands = build_boot_commands(encrypted_plan(architecture=architecture), "/target")
            self.assertFalse(commands.bios_required)
            self.assertEqual(len(commands.installs), 1)
            self.assertIn("--no-extra-removable", commands.installs[0])
            self.assertIn("--uefi-secure-boot", commands.installs[0])

    def test_live_installer_removal_preserves_encrypted_root_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            context = InstallContext(encrypted_plan(), lambda _: None, values={
                "target": target, "chroot_environment_ready": True,
            })
            for package in ENCRYPTED_ROOT_PACKAGES:
                runner.outputs[(
                    "chroot", str(target), "dpkg-query", "--show",
                    "--showformat=${db:Status-Abbrev}", package,
                )] = ("ii ", "", 0)
            RemoveLivePackagesStep(runner).execute(context)
            for package in ENCRYPTED_ROOT_PACKAGES:
                self.assertIn(
                    ("chroot", str(target), "apt-mark", "manual", package),
                    [command for command, _ in runner.commands],
                )

    def test_encrypted_linux_menu_and_efi_stub_use_both_volume_identities(self):
        inner_uuid = "abcdef01-1234-1234-1234-123456789abc"
        for filesystem in (Filesystem.BTRFS, Filesystem.EXT4):
            context = InstallContext(encrypted_plan(filesystem=filesystem), lambda _: None, values={
                "root_luks_uuid": LUKS_UUID, "root_filesystem_uuid": inner_uuid,
            })
            prefix = "/@root" if filesystem is Filesystem.BTRFS else ""
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                stub = target / "boot/efi/EFI/AnduinOS/grub.cfg"
                stub.parent.mkdir(parents=True)
                # The actual upstream grub-install format, including its
                # cryptouuid search hint and quoted relative prefix.
                stub.write_text(
                    f"cryptomount -u {LUKS_UUID}\n"
                    f"search.fs_uuid {inner_uuid} root cryptouuid/{LUKS_UUID.replace('-', '')}\n"
                    f"set prefix=($root)'{prefix}/boot/grub'\n"
                    "configfile $prefix/grub.cfg\n"
                )
                menu = (
                    "### BEGIN /etc/grub.d/10_linux ###\n"
                    f"linux\t{prefix}/boot/vmlinuz-test root=UUID={inner_uuid} rd.luks.uuid=luks-{LUKS_UUID}\n"
                    f"initrd\tnewc:/anduinos-unlock.key:{prefix}/etc/anduinos-luks/machine.key {prefix}/boot/initrd.img-test\n"
                    "### END /etc/grub.d/10_linux ###\n"
                )
                from installer_core.bootloader import _configure_graphical_unlock
                _configure_graphical_unlock(context, target)
                self.assertEqual(stub.read_text().split(), [
                    "anduinos_unlock", LUKS_UUID.replace("-", ""),
                    inner_uuid, prefix + "/boot/grub",
                ])
                _verify_encrypted_boot_output(context, target, menu)
                if filesystem is Filesystem.BTRFS:
                    recovery = "87654321-1234-1234-1234-123456789abc"
                    context.values["recovery_volume_uuid"] = recovery
                    _configure_graphical_unlock(context, target)
                    self.assertEqual(stub.read_text().split()[-1], recovery)
                    _verify_encrypted_boot_output(context, target, menu)
                broken = menu.replace("newc:/anduinos-unlock.key:", "missing:")
                with self.assertRaisesRegex(RuntimeError, "RAM credential"):
                    _verify_encrypted_boot_output(context, target, broken)

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
                                 execution_policy=ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST_PRO)
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

    def test_machine_setup_records_verified_credential_roles_in_the_luks_header(self):
        plan = encrypted_plan()
        volume = build_erase_disk_execution_plan(plan).commands.encrypted_volumes[0]
        token = dict(type="anduinos-credentials", version=1, keyslots=["0", "1", "2"],
                     roles=dict(password="0", recovery="1", machine="2"), pending=None)
        for readback_ok in (True, False):
            with self.subTest(readback_ok=readback_ok), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary)
                runner = RecordingCryptsetup()
                runner.outputs[("cryptsetup", "luksDump", "--dump-json-metadata", volume.source)] = (
                    json.dumps({"tokens": {"0": token}} if readback_ok else {}), "", 0)
                context = InstallContext(plan, lambda _: None,
                    encryption_credentials=EncryptionCredentials(PASSPHRASE, RECOVERY))
                context.values["opened_encrypted_volumes"] = [volume]
                if not readback_ok:
                    with self.assertRaisesRegex(RuntimeError, "credential roles"):
                        install_machine_key(context, runner, target)
                    self.assertFalse((target / MACHINE_KEY_PATH).exists())
                    continue
                install_machine_key(context, runner, target)
                key = target / MACHINE_KEY_PATH
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
                self.assertEqual(key.read_bytes(), runner.new_keys[0])
                self.assertEqual(len(key.read_bytes()), 64)
                self.assertIsNone(context.encryption_credentials)
                add = runner.commands[0][0]
                self.assertEqual(add[add.index("--new-key-slot") + 1], "2")
                imported = next(options["input_text"] for command, options in runner.commands
                                if command[:3] == ("cryptsetup", "token", "import"))
                self.assertEqual(json.loads(imported), token)

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
                    defaults = (target / ENCRYPTED_GRUB_PATH).read_text()
                    self.assertIn("export GRUB_ANDUINOS_CRYPT_HANDOFF=y", defaults)
                    self.assertIn("rd.luks.uuid=luks-" + LUKS_UUID, defaults)
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
