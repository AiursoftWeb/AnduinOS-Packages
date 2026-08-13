import unittest
from dataclasses import replace
from pathlib import Path

from test_storage_ui import workflow
from guided_test_plan_cli import inspect_workflow, require_safe_output
from installer_core.guided_test_plan import build_guided_vm_test_plan
from installer_core.model import (
    AuthenticationMode,
    Filesystem,
    InstallMode,
)
from installer_core.storage_ui import recommended_guided_selection
from installer_core.validation import (
    ExecutionPolicy,
    PlanValidationError,
    validate_plan_for_execution,
)


class GuidedVmTestPlanTests(unittest.TestCase):
    def test_builder_creates_a_passwordless_nonproduction_plan(self):
        model = workflow()
        selection = recommended_guided_selection(
            model.disks[0], Filesystem.BTRFS
        )
        plan = build_guided_vm_test_plan(model, selection)

        self.assertIs(plan.storage.mode, InstallMode.GUIDED_COEXISTENCE)
        self.assertIs(
            plan.identity.authentication,
            AuthenticationMode.PASSWORDLESS_SHARED,
        )
        self.assertTrue(plan.access.sudo_without_password)
        self.assertTrue(plan.access.automatic_login)
        self.assertFalse(plan.identity.password_hash)
        self.assertFalse(plan.software.install_updates)
        self.assertFalse(plan.software.install_third_party_drivers)
        self.assertFalse(plan.boot.install_fallback_path)
        with self.assertRaisesRegex(
            PlanValidationError, "password-protected account"
        ):
            validate_plan_for_execution(plan)
        validate_plan_for_execution(
            plan, ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST
        )

    def test_builder_honors_explicit_new_esp_and_ext4(self):
        model = workflow()
        selection = recommended_guided_selection(
            model.disks[0], Filesystem.EXT4
        )
        plan = build_guided_vm_test_plan(
            model,
            replace(selection, reused_esp_partuuid=""),
        )
        self.assertEqual(plan.storage.filesystem, Filesystem.EXT4)
        self.assertEqual(
            tuple(item.name for item in plan.storage.graph.partitions),
            ("efi-system", "swap", "root"),
        )

    def test_inspection_output_contains_stable_choices_but_no_commands(self):
        model = workflow()
        data = inspect_workflow(model)
        disk = data["disks"][0]
        self.assertEqual(
            disk["stable_id"], model.disks[0].disk.identity.stable_id
        )
        self.assertTrue(disk["guided_available"])
        self.assertTrue(disk["free_extents"][0]["extent_id"])
        self.assertEqual(
            disk["esp_candidates"][0]["partuuid"], "part-1"
        )
        self.assertNotIn("commands", repr(data).casefold())

    def test_plan_output_rejects_kernel_and_device_trees(self):
        for path in (
            Path("/dev/vda"),
            Path("/proc/test-plan.json"),
            Path("/sys/test-plan.json"),
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(RuntimeError, "cannot target"):
                    require_safe_output(path)
        require_safe_output(Path("/tmp/guided-plan.json"))


if __name__ == "__main__":
    unittest.main()
