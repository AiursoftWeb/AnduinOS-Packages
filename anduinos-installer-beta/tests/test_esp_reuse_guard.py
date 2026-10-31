"""The real ESP inspection must stop both preservation modes before writes."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fakes import FakeRunner
from test_guided_storage_graph import guided_plan
from test_manual_layout import selection
from test_manual_storage_graph import manual_plan
from installer_core.esp import NvramInspection, inspect_esp_for_reuse
from installer_core.manual_layout import ManualPartitionRequest, ManualPartitionRole
from installer_core.steps import InstallContext, StepRunner
from installer_core.storage_steps import PrepareStorageStep


class EspFixtureRunner(FakeRunner):
    """Populate a temporary directory instead of mounting any real device."""

    def __init__(self, files=(), directories=()):
        super().__init__()
        self.files = files
        self.directories = directories

    def run(self, command, **kwargs):
        result = super().run(command, **kwargs)
        if command[0] == "mount":
            root = Path(command[-1])
            for name in self.directories:
                (root / name).mkdir(parents=True, exist_ok=True)
            for name in self.files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"existing-boot-file")
        return result


class EspReuseGuardTests(unittest.TestCase):
    def storage_step(self, plan, inventory, runner, scratch_root):
        def inspect(esp, command_runner):
            return inspect_esp_for_reuse(
                esp, command_runner, scratch_root=scratch_root,
                statvfs=lambda _path: SimpleNamespace(
                    f_bavail=128, f_frsize=1024 * 1024,
                ),
            )

        return PrepareStorageStep(
            runner,
            inventory_probe=lambda: inventory,
            esp_inspector=inspect,
            nvram_inspector=lambda _runner: NvramInspection(True),
        )

    def test_occupied_namespace_stops_both_modes_before_any_execution(self):
        for factory in (guided_plan, manual_plan):
            for name in ("EFI/AnduinOS", "efi/ANDUINOS"):
                # Empty directories and partial-install leftovers are protected too.
                for filename in (None, "grub.cfg", "shimx64.efi", "shimaa64.efi"):
                    with self.subTest(mode=factory.__name__, name=name, file=filename):
                        plan, inventory = factory()
                        files = (f"{name}/{filename}",) if filename else ()
                        runner = EspFixtureRunner(files, (name,))
                        with tempfile.TemporaryDirectory() as directory:
                            step = self.storage_step(
                                plan, inventory, runner, Path(directory)
                            )
                            context = InstallContext(plan, lambda _message: None)
                            with patch.object(step, "execute") as execute:
                                result = StepRunner([step]).run(context)
                            execute.assert_not_called()
                            self.assertFalse(result.succeeded)
                            self.assertFalse(result.destructive_started)
                            self.assertNotIn("storage_execution_plan", context.values)
                            message = result.results[0].message
                            self.assertIn("already contains", message)
                            self.assertIn("Select another EFI System Partition", message)
                            self.assertIn("create a new one in unallocated space", message)
                            self.assertEqual(
                                [command[0] for command, _ in runner.commands],
                                ["fsck.fat", "mount", "umount"],
                            )

    def test_windows_and_ubuntu_esp_reuse_keeps_existing_command_plan(self):
        for factory in (guided_plan, manual_plan):
            plans = []
            for files in (
                (),
                ("EFI/Microsoft/Boot/bootmgfw.efi", "EFI/ubuntu/shimx64.efi",
                 "EFI/BOOT/BOOTX64.EFI"),
            ):
                with self.subTest(mode=factory.__name__, files=files):
                    plan, inventory = factory()
                    runner = EspFixtureRunner(files)
                    with tempfile.TemporaryDirectory() as directory:
                        step = self.storage_step(plan, inventory, runner, Path(directory))
                        context = InstallContext(plan, lambda _message: None)
                        step.preflight(context)
                        plans.append(context.values["storage_execution_plan"])
            self.assertEqual(plans[0], plans[1])

    def test_esp_read_failure_stops_preflight_and_unmounts(self):
        for factory in (guided_plan, manual_plan):
            with self.subTest(mode=factory.__name__):
                plan, inventory = factory()
                runner = EspFixtureRunner(("EFI/AnduinOS/grub.cfg",))
                with tempfile.TemporaryDirectory() as directory:
                    step = self.storage_step(plan, inventory, runner, Path(directory))
                    context = InstallContext(plan, lambda _message: None)
                    with patch("installer_core.esp._hash_file",
                               side_effect=PermissionError("Cannot read EFI file")):
                        with patch.object(step, "execute") as execute:
                            result = StepRunner([step]).run(context)
                    execute.assert_not_called()
                    self.assertFalse(result.succeeded)
                    self.assertFalse(result.destructive_started)
                    self.assertIn("Cannot read EFI file", result.results[0].message)
                    self.assertEqual(
                        [command[0] for command, _ in runner.commands],
                        ["fsck.fat", "mount", "umount"],
                    )

    def test_new_esp_does_not_inspect_or_reject_the_old_esp(self):
        chosen = selection(
            reused_esp="",
            new_partitions=(
                ManualPartitionRequest(ManualPartitionRole.EFI_SYSTEM, 81920, 82944),
                ManualPartitionRequest(ManualPartitionRole.ROOT, 82944, 112640),
            ),
        )
        for plan, inventory in (guided_plan(reuse_esp=False), manual_plan(chosen=chosen)):
            with self.subTest(mode=plan.storage.mode):
                runner = FakeRunner()
                inspector = Mock(side_effect=AssertionError("Old ESP must not be inspected"))
                step = PrepareStorageStep(
                    runner, inventory_probe=lambda: inventory,
                    esp_inspector=inspector,
                    nvram_inspector=lambda _runner: NvramInspection(True),
                )
                context = InstallContext(plan, lambda _message: None)
                step.preflight(context)
                inspector.assert_not_called()
                execution = context.values["storage_execution_plan"]
                self.assertFalse(execution.reuses_esp)
                self.assertNotEqual(execution.commands.devices["efi-system"],
                                    inventory.disks[0].partitions[0].identity.path)


if __name__ == "__main__":
    unittest.main()
