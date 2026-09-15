import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.language_support import InstallLanguagePacksStep
from installer_core.mirrors import SelectFastestAptMirrorStep
from installer_core.network import DetectNetworkConnectivityStep, RecheckNetworkConnectivityStep
from installer_core.software import (
    InstallMultimediaCodecsStep,
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
    def test_recovery_reaches_real_selected_package_steps(self):
        from test_software import CodecInstallRunner, prepare_apt

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_apt(target)
            release = target / "os-release"
            release.write_text("VERSION_CODENAME=resolute\n")
            detector = Mock(side_effect=[None, "http://archive.example/ubuntu/"])
            runner = CodecInstallRunner()
            context = InstallContext(
                valid_plan(install_multimedia_codecs=True),
                lambda _message: None,
                {"target": target, "chroot_environment_ready": True},
            )
            result = StepRunner([
                DetectNetworkConnectivityStep(os_release=release, detector=detector),
                RecheckNetworkConnectivityStep(os_release=release, detector=detector),
                RefreshPackageIndexesStep(runner),
                InstallMultimediaCodecsStep(runner),
                FinalOfflineStep(),
            ]).run(context)
        self.assertTrue(result.succeeded)
        self.assertEqual(detector.call_count, 2)
        self.assertTrue(context.values["multimedia_codecs_installed"])
        self.assertEqual([r.status for r in result.results], [
            StepStatus.WARNING, StepStatus.SUCCEEDED, StepStatus.SUCCEEDED,
            StepStatus.SUCCEEDED, StepStatus.SUCCEEDED,
        ])
        self.assertTrue(any("apt-get" in command for command, _ in runner.commands))

    def test_offline_mirror_is_skipped_and_pipeline_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            os_release = Path(directory) / "os-release"
            os_release.write_text(
                "VERSION_CODENAME=resolute\n", encoding="utf-8"
            )
            runner = FakeRunner()
            detector = Mock(return_value=None)
            statuses = []
            context = InstallContext(
                valid_plan(
                    install_third_party_drivers=True,
                    install_multimedia_codecs=True,
                ),
                lambda _message: None,
                {
                    "target": Path(directory),
                    "chroot_environment_ready": True,
                },
            )
            steps = [
                DetectNetworkConnectivityStep(
                    os_release=os_release,
                    detector=detector,
                ),
                RecheckNetworkConnectivityStep(
                    os_release=os_release,
                    detector=detector,
                ),
                SelectFastestAptMirrorStep(),
                InstallLanguagePacksStep(runner),
                RefreshPackageIndexesStep(runner),
                UpgradeSystemStep(runner),
                InstallMultimediaCodecsStep(runner),
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
        self.assertEqual(detector.call_count, 2)
        self.assertTrue(context.values["apt_mirror_preserved"])
        self.assertFalse(
            any(
                "apt-get" in command or "ubuntu-drivers" in command
                for command, _kwargs in runner.commands
            )
        )
        self.assertEqual(
            [item.status for item in result.results],
            [
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.SKIPPED,
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.WARNING,
                StepStatus.SUCCEEDED,
            ],
        )
        self.assertEqual(len(result.warnings), 7)
        terminal = [item for item in statuses if item[1] is not StepStatus.RUNNING]
        self.assertTrue(all(item[2] for item in terminal[:-1]))


if __name__ == "__main__":
    unittest.main()
