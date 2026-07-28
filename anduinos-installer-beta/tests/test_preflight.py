import unittest
from dataclasses import replace

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.preflight import PreflightError, verify_execution_environment
from installer_core.probe import PlatformProbe


class ExecutionPreflightTests(unittest.TestCase):
    def test_accepts_matching_platform_and_disk(self):
        plan = valid_plan()
        runner = FakeRunner()
        verify_execution_environment(
            plan,
            runner,
            platform_probe=lambda: PlatformProbe(
                plan.platform.architecture,
                plan.platform.firmware,
                plan.platform.secure_boot,
            ),
            disk_probe=lambda: (plan.storage.disk,),
        )
        self.assertTrue(runner.root_checked)

    def test_rejects_disk_substitution_at_same_path(self):
        plan = valid_plan()
        replacement = replace(plan.storage.disk, stable_id="serial:attacker")
        with self.assertRaisesRegex(PreflightError, "identity changed"):
            verify_execution_environment(
                plan,
                FakeRunner(),
                platform_probe=lambda: PlatformProbe(
                    plan.platform.architecture,
                    plan.platform.firmware,
                    plan.platform.secure_boot,
                ),
                disk_probe=lambda: (replacement,),
            )

    def test_rejects_secure_boot_state_change(self):
        plan = valid_plan()
        changed = replace(
            plan.platform,
            secure_boot=plan.platform.secure_boot.DISABLED,
        )
        with self.assertRaisesRegex(PreflightError, "Platform changed"):
            verify_execution_environment(
                plan,
                FakeRunner(),
                platform_probe=lambda: PlatformProbe(
                    changed.architecture,
                    changed.firmware,
                    changed.secure_boot,
                ),
                disk_probe=lambda: (plan.storage.disk,),
            )

