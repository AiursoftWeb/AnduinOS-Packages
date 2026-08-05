import unittest
from unittest.mock import patch

from helpers import valid_plan
from installer_core.executor import (
    InstallerExecutor,
    describe_installation_pipeline,
)
from test_guided_storage_graph import guided_plan


class CapturingStepRunner:
    captured = ()

    def __init__(self, steps, _progress, _status):
        type(self).captured = tuple(step.id for step in steps)

    def run(self, _context):
        return object()


class ExecutorPipelineTests(unittest.TestCase):
    def test_guided_pipeline_is_available_under_default_beta_policy(self):
        plan, _inventory = guided_plan()
        with patch("installer_core.executor.StepRunner", CapturingStepRunner):
            InstallerExecutor(lambda _message: None).run(plan)
        self.assertIn("prepare-storage", CapturingStepRunner.captured)
        self.assertIn("install-bootloader", CapturingStepRunner.captured)

    def test_software_and_secure_boot_order_is_fixed(self):
        with patch("installer_core.executor.StepRunner", CapturingStepRunner):
            InstallerExecutor(lambda _message: None).run(valid_plan())
        pipeline = CapturingStepRunner.captured
        self.assertEqual(
            pipeline[:3],
            (
                "detect-boot-environment",
                "detect-network-connectivity",
                "verify-target-disk",
            ),
        )
        expected = (
            "copy-system",
            "migrate-wifi-connection",
            "configure-keyboard-layout",
            "select-fastest-apt-mirror",
            "prepare-secure-boot",
            "install-language-packs",
            "install-input-method",
            "configure-system",
            "refresh-package-indexes",
            "upgrade-system",
            "ensure-waypoint",
            "verify-dkms-signatures",
            "install-bootloader",
            "enroll-secure-boot",
        )
        positions = tuple(pipeline.index(step) for step in expected)
        self.assertEqual(positions, tuple(sorted(positions)))
        self.assertLess(
            pipeline.index("migrate-wifi-connection"),
            pipeline.index("configure-storage"),
        )
        self.assertNotIn("install-third-party-drivers", pipeline)
        described = tuple(
            step_id
            for step_id, _weight in describe_installation_pipeline(
                valid_plan()
            )
        )
        self.assertEqual(described, pipeline)

    def test_optional_driver_step_is_only_present_when_selected(self):
        plan = valid_plan(install_third_party_drivers=True)
        with patch("installer_core.executor.StepRunner", CapturingStepRunner):
            InstallerExecutor(lambda _message: None).run(plan)
        self.assertIn(
            "install-third-party-drivers", CapturingStepRunner.captured
        )
        pipeline = CapturingStepRunner.captured
        self.assertLess(
            pipeline.index("prepare-secure-boot"),
            pipeline.index("install-third-party-drivers"),
        )
        self.assertLess(
            pipeline.index("install-third-party-drivers"),
            pipeline.index("verify-dkms-signatures"),
        )

    def test_waypoint_step_is_only_present_for_btrfs(self):
        from installer_core.model import Filesystem

        with patch("installer_core.executor.StepRunner", CapturingStepRunner):
            InstallerExecutor(lambda _message: None).run(
                valid_plan(filesystem=Filesystem.EXT4)
            )
        self.assertNotIn(
            "ensure-waypoint",
            CapturingStepRunner.captured,
        )

        with patch("installer_core.executor.StepRunner", CapturingStepRunner):
            InstallerExecutor(lambda _message: None).run(valid_plan())
        self.assertIn(
            "ensure-waypoint",
            CapturingStepRunner.captured,
        )
