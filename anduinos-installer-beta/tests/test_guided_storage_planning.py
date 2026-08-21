import unittest
from dataclasses import replace
from unittest.mock import patch

from test_guided_storage_graph import guided_plan
from installer_core.esp import (
    GUIDED_ESP_MINIMUM_FREE_BYTES,
    EspReuseInspection,
    NvramInspection,
)
from installer_core.model import Filesystem
from installer_core.storage_commands import StorageCommandPlan
from installer_core.storage_graph import StorageGraphAction
from installer_core.storage_planning import (
    build_guided_coexistence_execution_plan,
)
from installer_core.storage_write_set import StorageAction
from installer_core.validation import PlanValidationError


def healthy_esp(plan, inventory, *, free_bytes=None):
    esp = inventory.disks[0].partitions[0]
    return EspReuseInspection(
        partuuid=esp.identity.partuuid,
        filesystem_uuid=esp.filesystem_uuid,
        healthy=True,
        free_bytes=(
            GUIDED_ESP_MINIMUM_FREE_BYTES
            if free_bytes is None
            else free_bytes
        ),
    )


def build_execution(*, reuse_esp=True, filesystem=Filesystem.BTRFS):
    plan, inventory = guided_plan(
        reuse_esp=reuse_esp,
        filesystem=filesystem,
    )
    execution = build_guided_coexistence_execution_plan(
        plan,
        inventory,
        esp_inspection=(
            healthy_esp(plan, inventory) if reuse_esp else None
        ),
        nvram_inspection=NvramInspection(available=True),
    )
    return plan, inventory, execution


class GuidedCoexistenceExecutionPlanTests(unittest.TestCase):
    def test_reused_esp_is_preserved_and_never_formatted(self):
        plan, inventory, execution = build_execution()
        disk = inventory.disks[0]
        commands = execution.commands
        self.assertTrue(execution.reuses_esp)
        self.assertEqual(commands.devices["efi-system"], "/dev/nvme0n1p1")
        creates = [item for item in commands.partition if "mkpart" in item]
        self.assertEqual(len(creates), 2)
        arguments = {arg for item in commands.partition for arg in item}
        self.assertNotIn("mklabel", arguments)
        self.assertNotIn(
            commands.devices["efi-system"],
            {item[-1] for item in commands.format},
        )
        preserves = tuple(
            item
            for item in execution.write_set.operations
            if item.action is StorageAction.PRESERVE
        )
        self.assertEqual(len(preserves), len(disk.partitions))
        self.assertEqual(
            tuple(
                (item.action.value, item.target_id)
                for item in execution.write_set.operations
            ),
            tuple(
                (item.action.value, item.target_id)
                for item in plan.storage.graph.operations
            ),
        )

    def test_new_esp_is_created_and_formatted_inside_extent(self):
        _plan, _inventory, execution = build_execution(reuse_esp=False)
        self.assertFalse(execution.reuses_esp)
        creates = [
            item for item in execution.commands.partition if "mkpart" in item
        ]
        self.assertEqual(len(creates), 3)
        self.assertEqual(len(execution.commands.format), 3)
        self.assertTrue(
            any(item[0] == "mkfs.vfat" for item in execution.commands.format)
        )

    def test_commands_use_current_paths_after_stable_identity_resolution(self):
        plan, inventory = guided_plan()
        disk = inventory.disks[0]
        current_partitions = tuple(
            replace(
                item,
                identity=replace(
                    item.identity,
                    path=item.identity.path.replace("nvme0n1", "nvme4n1"),
                ),
            )
            for item in disk.partitions
        )
        current_disk = replace(
            disk,
            identity=replace(disk.identity, path="/dev/nvme4n1"),
            partitions=current_partitions,
        )
        current_inventory = replace(inventory, disks=(current_disk,))
        inspection = EspReuseInspection(
            partuuid=current_partitions[0].identity.partuuid,
            filesystem_uuid=current_partitions[0].filesystem_uuid,
            healthy=True,
            free_bytes=GUIDED_ESP_MINIMUM_FREE_BYTES,
        )
        execution = build_guided_coexistence_execution_plan(
            plan,
            current_inventory,
            esp_inspection=inspection,
            nvram_inspection=NvramInspection(available=True),
        )
        self.assertEqual(
            execution.commands.devices["efi-system"],
            "/dev/nvme4n1p1",
        )
        self.assertTrue(
            all(
                command[2] == "/dev/nvme4n1"
                for command in execution.commands.partition
            )
        )
        self.assertIn("/dev/nvme4n1", execution.boot_commands.nvram_create)

    def test_shared_esp_health_identity_and_capacity_are_mandatory(self):
        plan, inventory = guided_plan()
        base = healthy_esp(plan, inventory)
        cases = (
            (replace(base, partuuid="other"), "identity changed"),
            (replace(base, healthy=False, reason="dirty FAT"), "not healthy"),
            (
                replace(base, free_bytes=GUIDED_ESP_MINIMUM_FREE_BYTES - 1),
                "at least 64 MiB",
            ),
        )
        for inspection, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    build_guided_coexistence_execution_plan(
                        plan,
                        inventory,
                        esp_inspection=inspection,
                        nvram_inspection=NvramInspection(available=True),
                    )

    def test_nvram_failure_blocks_new_or_reused_esp_plans(self):
        for reuse_esp in (True, False):
            plan, inventory = guided_plan(reuse_esp=reuse_esp)
            with self.subTest(reuse_esp=reuse_esp):
                with self.assertRaisesRegex(RuntimeError, "firmware boot entry"):
                    build_guided_coexistence_execution_plan(
                        plan,
                        inventory,
                        esp_inspection=(
                            healthy_esp(plan, inventory)
                            if reuse_esp
                            else None
                        ),
                        nvram_inspection=NvramInspection(
                            available=False,
                            reason="efivarfs is read-only",
                        ),
                    )

    def test_boot_commands_use_vendor_directory_without_fallback(self):
        _plan, _inventory, execution = build_execution()
        boot = execution.boot_commands
        self.assertIn("--no-extra-removable", boot.install)
        self.assertIn("--no-nvram", boot.install)
        self.assertFalse(any("i386-pc" in item for item in boot.install))
        self.assertFalse(any("EFI\\BOOT" in item for item in boot.install))
        self.assertEqual(boot.loader_path, r"\EFI\AnduinOS\shimx64.efi")
        self.assertEqual(boot.nvram_create[-1], boot.loader_path)

    def test_ext4_formats_only_the_new_root_and_swap(self):
        _plan, _inventory, execution = build_execution(
            filesystem=Filesystem.EXT4
        )
        formats = {item[0] for item in execution.commands.format}
        self.assertEqual(formats, {"mkswap", "mkfs.ext4"})
        self.assertFalse(
            any(
                item.action is StorageAction.CREATE_SUBVOLUME
                for item in execution.write_set.operations
            )
        )

    def test_forbidden_partition_command_injection_fails_closed(self):
        plan, inventory = guided_plan()
        from installer_core.storage_commands import (
            build_guided_coexistence_storage_commands,
        )

        commands = build_guided_coexistence_storage_commands(plan, inventory)
        drifted = StorageCommandPlan(
            partition=(
                ("parted", "--script", "/dev/nvme0n1", "mklabel", "gpt"),
                *commands.partition,
            ),
            format=commands.format,
            devices=commands.devices,
        )
        with patch(
            "installer_core.storage_planning."
            "build_guided_coexistence_storage_commands",
            return_value=drifted,
        ):
            with self.assertRaisesRegex(RuntimeError, "forbidden table edit"):
                build_guided_coexistence_execution_plan(
                    plan,
                    inventory,
                    esp_inspection=healthy_esp(plan, inventory),
                    nvram_inspection=NvramInspection(available=True),
                )

    def test_existing_esp_format_injection_fails_closed(self):
        plan, inventory = guided_plan()
        from installer_core.storage_commands import (
            build_guided_coexistence_storage_commands,
        )

        commands = build_guided_coexistence_storage_commands(plan, inventory)
        drifted = StorageCommandPlan(
            partition=commands.partition,
            format=(
                *commands.format,
                (
                    "mkfs.vfat",
                    "-F",
                    "32",
                    "-n",
                    "ATTACK",
                    commands.devices["efi-system"],
                ),
            ),
            devices=commands.devices,
        )
        with patch(
            "installer_core.storage_planning."
            "build_guided_coexistence_storage_commands",
            return_value=drifted,
        ):
            with self.assertRaisesRegex(RuntimeError, "format commands drifted"):
                build_guided_coexistence_execution_plan(
                    plan,
                    inventory,
                    esp_inspection=healthy_esp(plan, inventory),
                    nvram_inspection=NvramInspection(available=True),
                )

    def test_write_set_drift_fails_closed(self):
        plan, inventory, execution = build_execution()
        drifted = replace(
            execution.write_set,
            operations=execution.write_set.operations[:-1],
        )
        with patch(
            "installer_core.storage_planning."
            "build_guided_coexistence_write_set",
            return_value=drifted,
        ):
            with self.assertRaisesRegex(RuntimeError, "write set drifted"):
                build_guided_coexistence_execution_plan(
                    plan,
                    inventory,
                    esp_inspection=healthy_esp(plan, inventory),
                    nvram_inspection=NvramInspection(available=True),
                )

    def test_graph_contains_exactly_one_nvram_intent(self):
        plan, _inventory, execution = build_execution()
        graph_nvram = [
            item
            for item in plan.storage.graph.operations
            if item.action is StorageGraphAction.UPDATE_NVRAM
        ]
        writes = [
            item
            for item in execution.write_set.operations
            if item.action is StorageAction.UPDATE_NVRAM
        ]
        self.assertEqual(len(graph_nvram), 1)
        self.assertEqual(len(writes), 1)

    def test_compiler_validates_the_complete_non_storage_plan(self):
        plan, inventory = guided_plan()
        invalid = replace(
            plan,
            identity=replace(plan.identity, username="root"),
        )
        with self.assertRaisesRegex(PlanValidationError, "Reserved username"):
            build_guided_coexistence_execution_plan(
                invalid,
                inventory,
                esp_inspection=healthy_esp(plan, inventory),
                nvram_inspection=NvramInspection(available=True),
            )


if __name__ == "__main__":
    unittest.main()
