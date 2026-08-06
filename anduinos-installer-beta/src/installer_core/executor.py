"""Composition root for the new privileged installer backend."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .command import CommandRunner
from .chroot_env import EnterChrootStep, LeaveChrootStep
from .bootloader import InstallBootloaderStep
from .secure_boot import (
    EnrollSecureBootStep,
    PrepareSecureBootStep,
    VerifyDkmsSignaturesStep,
)
from .software import (
    InstallMultimediaCodecsStep,
    InstallThirdPartyDriversStep,
    RefreshPackageIndexesStep,
    UpgradeSystemStep,
)
from .execution_steps import (
    CopySystemStep,
    DetectBootEnvironmentStep,
    UnmountTargetStep,
    VerifyTargetDiskStep,
)
from .language_support import InstallLanguagePacksStep
from .live_cleanup import RemoveLivePackagesStep
from .mirrors import SelectFastestAptMirrorStep
from .network import DetectNetworkConnectivityStep
from .model import Filesystem, Firmware, InstallPlan
from .other_systems import CheckOtherDiskSystemsStep
from .regional_config import ConfigureKeyboardStep, InstallInputMethodStep
from .steps import (
    InstallContext,
    InstallResult,
    InstallStep,
    StepRunner,
    StepStatus,
)
from .storage_steps import MountTargetStep, PrepareStorageStep
from .system_config import ConfigureSystemStep
from .target_config import ConfigureStorageStep
from .waypoint import EnsureWaypointStep
from .wifi_migration import MigrateWifiConnectionStep
from .validation import ExecutionPolicy, validate_plan_for_execution


class InstallerExecutor:
    """Execute the fixed release-one pipeline for an immutable plan."""

    def __init__(
        self,
        log: Callable[[str], None],
        progress: Callable[[str, int, int], None] | None = None,
        status: Callable[[str, StepStatus, str], None] | None = None,
        *,
        target: Path = Path("/target"),
        runner: CommandRunner | None = None,
        execution_policy: ExecutionPolicy = ExecutionPolicy.RELEASE,
    ):
        self.log = log
        self.progress = progress
        self.status = status
        self.target = target
        self.runner = runner or CommandRunner(log)
        self.execution_policy = execution_policy

    def run(self, plan: InstallPlan) -> InstallResult:
        validate_plan_for_execution(plan, self.execution_policy)
        context = InstallContext(
            plan,
            self.log,
            execution_policy=self.execution_policy,
        )
        steps = self.build_steps(plan)
        return StepRunner(steps, self.progress, self.status).run(context)

    def build_steps(self, plan: InstallPlan) -> list[InstallStep]:
        """Build the canonical ordered pipeline without executing it."""

        steps: list[InstallStep] = [
            DetectBootEnvironmentStep(self.runner),
            DetectNetworkConnectivityStep(),
            VerifyTargetDiskStep(self.runner),
            PrepareStorageStep(self.runner, target=self.target),
            MountTargetStep(self.runner, target=self.target),
            CopySystemStep(self.runner),
            MigrateWifiConnectionStep(self.runner),
            ConfigureStorageStep(self.runner),
            EnterChrootStep(self.runner, target=self.target),
            RemoveLivePackagesStep(self.runner),
            ConfigureKeyboardStep(),
            SelectFastestAptMirrorStep(),
            # Establish the target-owned DKMS key before any package operation
            # can build kernel modules. Never let an upgrade inherit the
            # copied Live image's signing identity.
            PrepareSecureBootStep(self.runner),
            InstallLanguagePacksStep(self.runner),
            InstallInputMethodStep(self.runner),
        ]
        if plan.software.install_multimedia_codecs:
            steps.append(InstallMultimediaCodecsStep(self.runner))
        steps.append(ConfigureSystemStep(self.runner))
        if plan.software.install_updates:
            steps.extend(
                (
                    RefreshPackageIndexesStep(self.runner),
                    UpgradeSystemStep(self.runner),
                )
            )
        if plan.storage.filesystem is Filesystem.BTRFS:
            steps.append(EnsureWaypointStep(self.runner))
        if plan.software.install_third_party_drivers:
            steps.append(InstallThirdPartyDriversStep(self.runner))
        steps.extend(
            (
                VerifyDkmsSignaturesStep(self.runner),
                InstallBootloaderStep(self.runner),
                EnrollSecureBootStep(self.runner),
            )
        )
        if plan.platform.firmware is Firmware.UEFI:
            steps.append(CheckOtherDiskSystemsStep(self.runner))
        steps.extend(
            (
                LeaveChrootStep(self.runner),
                UnmountTargetStep(self.runner),
            )
        )
        return steps


def describe_installation_pipeline(
    plan: InstallPlan,
) -> tuple[tuple[str, int], ...]:
    """Return the production step IDs and weights for UI simulation."""

    executor = InstallerExecutor(lambda _message: None)
    return tuple(
        (step.id, max(1, step.progress_weight))
        for step in executor.build_steps(plan)
    )
