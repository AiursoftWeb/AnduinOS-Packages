import unittest
from unittest.mock import patch

from helpers import valid_plan
from installer_core.executor import InstallerExecutor


class CapturingStepRunner:
    captured = ()

    def __init__(self, steps, _progress, _status):
        type(self).captured = tuple(step.id for step in steps)

    def run(self, _context):
        return object()


class ExecutorPipelineTests(unittest.TestCase):
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
            "configure-system",
            "select-fastest-apt-mirror",
            "prepare-secure-boot",
            "refresh-package-indexes",
            "upgrade-system",
            "verify-dkms-signatures",
            "install-bootloader",
            "enroll-secure-boot",
        )
        positions = tuple(pipeline.index(step) for step in expected)
        self.assertEqual(positions, tuple(sorted(positions)))
        self.assertNotIn("install-third-party-drivers", pipeline)

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
