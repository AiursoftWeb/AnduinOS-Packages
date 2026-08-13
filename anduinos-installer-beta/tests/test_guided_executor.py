import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from executor_cli import (
    GUIDED_TEST_ENVIRONMENT,
    GUIDED_TEST_FLAG,
    execution_policy,
)
from fakes import FakeRunner
from test_guided_storage_graph import guided_plan
from test_guided_storage_planning import healthy_esp
from installer_core.esp import NvramInspection
from installer_core.executor import InstallerExecutor
from installer_core.model import AuthenticationMode
from installer_core.storage_inventory import (
    EFI_SYSTEM_PARTITION_GUID,
    PartitionIdentity,
    PartitionInventory,
    StorageInventory,
)
from installer_core.steps import InstallContext
from installer_core.steps import StepRunner
from installer_core.storage_steps import PrepareStorageStep
from installer_core.validation import (
    ExecutionPolicy,
    PlanValidationError,
    validate_plan_for_execution,
)


def post_write_inventory(plan, inventory):
    disk = inventory.disks[0]
    graph = plan.storage.graph
    filesystems = {
        item.block_id: item.filesystem.value for item in graph.filesystems
    }
    additions = tuple(
        PartitionInventory(
            identity=PartitionIdentity(
                path=f"{disk.identity.path}p{item.number}",
                number=item.number,
                partuuid=f"new-part-{item.number}",
                start_bytes=item.start_mib * 1024 * 1024,
                size_bytes=(item.end_mib - item.start_mib) * 1024 * 1024,
            ),
            parent_disk_id=disk.identity.stable_id,
            partition_type=(
                EFI_SYSTEM_PARTITION_GUID
                if item.name == "efi-system"
                else "linux-test"
            ),
            filesystem_type=filesystems[item.partition_id],
            filesystem_uuid=f"new-fs-{item.number}",
            flags=item.flags,
        )
        for item in graph.partitions
    )
    updated = replace(
        disk,
        partitions=(*disk.partitions, *additions),
        free_extents=(),
        topology_digest="f" * 64,
    )
    return StorageInventory((updated,), "9" * 64)


class GuidedExecutorGateTests(unittest.TestCase):
    def test_public_executor_launcher_cannot_forward_test_flag(self):
        launcher = (
            Path(__file__).parents[1]
            / "assets/anduinos-installer-executor"
        ).read_text()
        self.assertIn('if [ "$#" -ne 0 ]', launcher)
        self.assertNotIn('executor_cli.py "$@"', launcher)

    def test_cli_requires_both_test_authorizations(self):
        self.assertEqual(
            execution_policy([], {}),
            ExecutionPolicy.RELEASE,
        )
        with self.assertRaisesRegex(ValueError, "require both"):
            execution_policy([GUIDED_TEST_FLAG], {})
        with self.assertRaisesRegex(ValueError, "require both"):
            execution_policy([], {GUIDED_TEST_ENVIRONMENT: "1"})
        self.assertEqual(
            execution_policy(
                [GUIDED_TEST_FLAG],
                {GUIDED_TEST_ENVIRONMENT: "1"},
            ),
            ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST,
        )

    def test_password_protected_guided_plan_uses_release_policy(self):
        plan, _inventory = guided_plan()
        validate_plan_for_execution(plan)
        validate_plan_for_execution(
            plan, ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST
        )

    def test_passwordless_guided_plan_remains_vm_test_only(self):
        plan, _inventory = guided_plan()
        passwordless = replace(
            plan,
            identity=replace(
                plan.identity,
                authentication=AuthenticationMode.PASSWORDLESS_SHARED,
                password_hash="",
            ),
            access=replace(
                plan.access,
                sudo_without_password=True,
                automatic_login=True,
            ),
        )
        with self.assertRaisesRegex(
            PlanValidationError, "password-protected account"
        ):
            validate_plan_for_execution(passwordless)
        validate_plan_for_execution(
            passwordless, ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST
        )

    def test_default_executor_constructs_guided_pipeline(self):
        plan, _inventory = guided_plan()
        with patch("installer_core.executor.StepRunner") as step_runner:
            InstallerExecutor(lambda _message: None).run(plan)
        step_runner.assert_called_once()


class GuidedPrepareStorageTests(unittest.TestCase):
    def context(self, plan):
        return InstallContext(
            plan,
            lambda _message: None,
            execution_policy=ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST,
        )

    def test_preflight_freezes_then_executes_only_declared_new_storage(self):
        plan, inventory = guided_plan()
        after = post_write_inventory(plan, inventory)
        inventories = iter((inventory, after))
        runner = FakeRunner()
        inspection = healthy_esp(plan, inventory)
        step = PrepareStorageStep(
            runner,
            inventory_probe=lambda: next(inventories),
            esp_inspector=lambda _esp, _runner: inspection,
            nvram_inspector=lambda _runner: NvramInspection(True),
        )
        context = self.context(plan)
        logs = []
        context.log = logs.append

        step.preflight(context)
        frozen = context.values["guided_storage_execution_plan"]
        self.assertIs(context.values["storage_execution_plan"], frozen)
        self.assertIn("guided_preservation_snapshot", context.values)
        self.assertFalse(runner.commands)

        for name, device in frozen.commands.devices.items():
            filesystem = {
                "efi-system": "vfat",
                "swap": "swap",
                "root": plan.storage.filesystem.value,
            }[name]
            runner.outputs[
                ("blkid", "-s", "TYPE", "-o", "value", device)
            ] = (filesystem + "\n", "", 0)
        with patch(
            "installer_core.storage_steps.Path.exists", return_value=True
        ):
            step.execute(context)
        step.verify(context)

        commands = [item[0] for item in runner.commands]
        flattened = {argument for command in commands for argument in command}
        self.assertNotIn("mklabel", flattened)
        self.assertNotIn("rm", flattened)
        self.assertNotIn("resizepart", flattened)
        formatted = {
            command[-1]
            for command in commands
            if command[0].startswith("mkfs") or command[0] == "mkswap"
        }
        self.assertNotIn("/dev/nvme0n1p1", formatted)
        expected_boundaries = [
            *(f"guided-partition-command-{index + 1}"
              for index in range(len(frozen.commands.partition))),
            *(f"guided-format-{name}"
              for name in ("swap", "root")),
        ]
        for boundary in expected_boundaries:
            before = f"[anduinos-boundary:{boundary}:before]"
            after_marker = f"[anduinos-boundary:{boundary}:after]"
            self.assertIn(before, logs)
            self.assertIn(after_marker, logs)
            self.assertLess(logs.index(before), logs.index(after_marker))

    def test_guided_execute_cannot_skip_all_step_preflight(self):
        plan, _inventory = guided_plan()
        runner = FakeRunner()
        with self.assertRaisesRegex(RuntimeError, "all-step preflight"):
            PrepareStorageStep(runner).execute(self.context(plan))
        self.assertFalse(runner.commands)

    def test_post_write_preservation_drift_is_fatal(self):
        plan, inventory = guided_plan()
        after = post_write_inventory(plan, inventory)
        changed_windows = replace(
            after.disks[0].partitions[2],
            identity=replace(
                after.disks[0].partitions[2].identity,
                size_bytes=after.disks[0].partitions[2].identity.size_bytes - 1,
            ),
        )
        after = replace(
            after,
            disks=(
                replace(
                    after.disks[0],
                    partitions=(
                        *after.disks[0].partitions[:2],
                        changed_windows,
                        *after.disks[0].partitions[3:],
                    ),
                ),
            ),
        )
        inventories = iter((inventory, after))
        runner = FakeRunner()
        step = PrepareStorageStep(
            runner,
            inventory_probe=lambda: next(inventories),
            esp_inspector=lambda _esp, _runner: healthy_esp(plan, inventory),
            nvram_inspector=lambda _runner: NvramInspection(True),
        )
        context = self.context(plan)
        step.preflight(context)
        frozen = context.values["guided_storage_execution_plan"]
        context.values["partition_devices"] = frozen.commands.devices
        for name, device in frozen.commands.devices.items():
            filesystem = {
                "efi-system": "vfat",
                "swap": "swap",
                "root": plan.storage.filesystem.value,
            }[name]
            runner.outputs[
                ("blkid", "-s", "TYPE", "-o", "value", device)
            ] = (filesystem + "\n", "", 0)
        with self.assertRaisesRegex(RuntimeError, "Preserved partition changed"):
            step.verify(context)

    def test_each_new_partition_and_format_boundary_stops_the_pipeline(self):
        plan, inventory = guided_plan()
        inspection = healthy_esp(plan, inventory)
        probe_step = PrepareStorageStep(
            FakeRunner(),
            inventory_probe=lambda: inventory,
            esp_inspector=lambda _esp, _runner: inspection,
            nvram_inspector=lambda _runner: NvramInspection(True),
        )
        probe_context = self.context(plan)
        probe_step.preflight(probe_context)
        execution = probe_context.values["guided_storage_execution_plan"]
        boundaries = (
            *execution.commands.partition,
            *execution.commands.format,
        )

        for failed in boundaries:
            with self.subTest(command=failed):
                runner = FakeRunner()
                original_run = runner.run

                def run(command, **kwargs):
                    if tuple(command) == failed:
                        runner.commands.append((tuple(command), kwargs))
                        raise RuntimeError("injected guided boundary failure")
                    return original_run(command, **kwargs)

                runner.run = run
                step = PrepareStorageStep(
                    runner,
                    inventory_probe=lambda: inventory,
                    esp_inspector=lambda _esp, _runner: inspection,
                    nvram_inspector=lambda _runner: NvramInspection(True),
                )
                with patch(
                    "installer_core.storage_steps.Path.exists",
                    return_value=True,
                ):
                    result = StepRunner([step]).run(self.context(plan))
                self.assertFalse(result.succeeded)
                self.assertTrue(result.destructive_started)
                commands = [item[0] for item in runner.commands]
                attempted = [item for item in commands if item in boundaries]
                self.assertEqual(
                    attempted,
                    list(boundaries[: boundaries.index(failed) + 1]),
                )


if __name__ == "__main__":
    unittest.main()
