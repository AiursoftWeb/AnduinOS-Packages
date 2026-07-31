import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.mirrors import SelectFastestAptMirrorStep
from installer_core.network import DetectNetworkConnectivityStep
from installer_core.software import (
    InstallThirdPartyDriversStep,
    RefreshPackageIndexesStep,
    UpgradeSystemStep,
)
from installer_core.steps import (
    FailurePolicy,
    InstallContext,
    StepRunner,
    StepStatus,
)


class FinalOfflineStep:
    id = "continue-offline-installation"
    title = "Continue offline installation"
    failure_policy = FailurePolicy.FATAL
    progress_weight = 1
    destructive = False

    def preflight(self, _context):
        return None

    def execute(self, context):
        context.values["offline_pipeline_continued"] = True

    def verify(self, context):
        if not context.values.get("offline_pipeline_continued"):
            raise RuntimeError("Offline pipeline did not continue")

    def cleanup(self, _context):
        return None


class OfflinePipelineTests(unittest.TestCase):
    def test_every_online_only_step_warns_and_pipeline_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            os_release = Path(directory) / "os-release"
            os_release.write_text(
                "VERSION_CODENAME=resolute\n", encoding="utf-8"
            )
            runner = FakeRunner()
            statuses = []
            context = InstallContext(
                valid_plan(install_third_party_drivers=True),
                lambda _message: None,
            )
            steps = [
                DetectNetworkConnectivityStep(
                    os_release=os_release,
                    detector=lambda _codename: None,
                ),
                SelectFastestAptMirrorStep(),
                RefreshPackageIndexesStep(runner),
                UpgradeSystemStep(runner),
                InstallThirdPartyDriversStep(runner),
                FinalOfflineStep(),
            ]
            result = StepRunner(
                steps,
                status=lambda step, status, message: statuses.append(
                    (step, status, message)
                ),
            ).run(context)

        self.assertTrue(result.succeeded)
        self.assertTrue(context.values["offline_pipeline_continued"])
        self.assertEqual(runner.commands, [])
        self.assertEqual(
            [item.status for item in result.results],
            [
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.SUCCEEDED,
            ],
        )
        terminal = [item for item in statuses if item[1] is not StepStatus.RUNNING]
        self.assertTrue(all(item[2] for item in terminal[:-1]))


if __name__ == "__main__":
    unittest.main()
