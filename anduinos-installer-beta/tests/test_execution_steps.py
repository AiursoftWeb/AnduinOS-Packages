import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import (
    TEST_PHYSICAL_MEMORY_BYTES,
    valid_inventory,
    valid_plan,
)
from installer_core.execution_steps import (
    CopySystemStep,
    DetectBootEnvironmentStep,
    UnmountTargetStep,
    VerifyTargetDiskStep,
)
from installer_core.model import Firmware, SecureBoot, SourceSpec
from installer_core.probe import PlatformProbe
from installer_core.steps import InstallContext


class CopySystemTests(unittest.TestCase):
    def test_preflight_requires_existing_source(self):
        plan = replace(
            valid_plan(),
            source=SourceSpec(image_path="/definitely/missing.squashfs"),
        )
        with self.assertRaisesRegex(RuntimeError, "System image not found"):
            CopySystemStep(FakeRunner()).preflight(
                InstallContext(plan, lambda _message: None)
            )

    def test_execute_and_verify_target(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "filesystem.squashfs"
            source.touch()
            target = root / "target"
            (target / "etc").mkdir(parents=True)
            (target / "etc/os-release").touch()
            (target / "usr").mkdir()
            (target / "var").mkdir()
            plan = replace(
                valid_plan(), source=SourceSpec(image_path=str(source))
            )
            context = InstallContext(
                plan, lambda _message: None, values={"target": target}
            )
            step = CopySystemStep(runner)
            step.preflight(context)
            step.execute(context)
            step.verify(context)
        self.assertEqual(runner.commands[-1][0][0], "unsquashfs")


class EnvironmentReportingTests(unittest.TestCase):
    def test_legacy_bios_and_secure_boot_state_are_explicit(self):
        plan = valid_plan(
            firmware=Firmware.BIOS,
            secure_boot=SecureBoot.NOT_APPLICABLE,
        )
        logs = []
        step = DetectBootEnvironmentStep(
            FakeRunner(),
            platform_probe=lambda: PlatformProbe(
                plan.platform.architecture,
                plan.platform.firmware,
                plan.platform.secure_boot,
            ),
        )
        context = InstallContext(plan, logs.append)
        step.preflight(context)
        step.execute(context)
        output = "\n".join(logs)
        self.assertIn("Firmware mode: Legacy BIOS", output)
        self.assertIn("Secure Boot: not applicable", output)
        self.assertIn("UEFI Boot#### entries: will not be modified", output)

    def test_uefi_secure_boot_enabled_is_explicit(self):
        plan = valid_plan()
        logs = []
        step = DetectBootEnvironmentStep(
            FakeRunner(),
            platform_probe=lambda: PlatformProbe(
                plan.platform.architecture,
                plan.platform.firmware,
                plan.platform.secure_boot,
            ),
        )
        context = InstallContext(plan, logs.append)
        step.preflight(context)
        step.execute(context)
        output = "\n".join(logs)
        self.assertIn("Firmware mode: UEFI", output)
        self.assertIn("Secure Boot: enabled", output)

    def test_uefi_without_secure_boot_support_is_explicit(self):
        plan = valid_plan(secure_boot=SecureBoot.UNSUPPORTED)
        logs = []
        step = DetectBootEnvironmentStep(
            FakeRunner(),
            platform_probe=lambda: PlatformProbe(
                plan.platform.architecture,
                plan.platform.firmware,
                plan.platform.secure_boot,
            ),
        )
        context = InstallContext(plan, logs.append)
        step.preflight(context)
        step.execute(context)
        self.assertIn("Secure Boot: unsupported by firmware", "\n".join(logs))

    def test_target_disk_log_excludes_other_operating_systems(self):
        plan = valid_plan()
        logs = []
        runner = FakeRunner()
        runner.outputs[
            (
                "lsblk",
                "--json",
                "--paths",
                "--output",
                "PATH,TYPE,MOUNTPOINTS",
                plan.storage.disk.path,
            )
        ] = (
            '{"blockdevices":[{"path":"/dev/nvme0n1","type":"disk",'
            '"mountpoints":[null]}]}',
            "",
            0,
        )
        step = VerifyTargetDiskStep(
            runner,
            inventory_probe=lambda: valid_inventory(plan),
            physical_memory_probe=lambda: TEST_PHYSICAL_MEMORY_BYTES,
        )
        context = InstallContext(plan, logs.append)
        step.preflight(context)
        step.execute(context)
        output = "\n".join(logs)
        self.assertIn("Only the selected disk", output)
        self.assertIn("Other disks and their EFI System Partitions", output)
        self.assertIn("will be checked read-only", output)
        self.assertIn("added to the AnduinOS GRUB menu", output)


class UnmountTargetTests(unittest.TestCase):
    def test_unmounts_children_first_and_clears_state(self):
        runner = FakeRunner()
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            values={
                "target": Path("/target-test"),
                "target_efi_mounted": True,
                "target_root_mounted": True,
            },
        )
        step = UnmountTargetStep(runner)
        step.execute(context)
        step.verify(context)
        self.assertEqual(
            [item[0] for item in runner.commands],
            [
                ("umount", "/target-test/boot/efi"),
                ("umount", "/target-test"),
                ("swapon", "--show=NAME", "--noheadings", "--raw"),
            ],
        )

    def test_successful_unmount_deactivates_target_swap(self):
        runner = FakeRunner()
        runner.outputs[
            ("swapon", "--show=NAME", "--noheadings", "--raw")
        ] = ("/dev/nvme0n1p3\n", "", 0)
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            values={
                "target": Path("/target-test"),
                "partition_devices": {"swap": "/dev/nvme0n1p3"},
            },
        )

        UnmountTargetStep(runner).execute(context)

        self.assertIn(
            ("swapoff", "/dev/nvme0n1p3"),
            [item[0] for item in runner.commands],
        )
