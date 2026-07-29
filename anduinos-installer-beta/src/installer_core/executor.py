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
    InstallThirdPartyDriversStep,
    RefreshPackageIndexesStep,
    UpgradeSystemStep,
)
from .execution_steps import (
    CopySystemStep,
    UnmountTargetStep,
    VerifyEnvironmentStep,
)
from .live_cleanup import CleanupLiveSystemStep
from .mirrors import SelectFastestAptMirrorStep
from .model import InstallPlan
from .steps import InstallContext, InstallResult, StepRunner, StepStatus
from .storage_steps import MountTargetStep, PrepareStorageStep
from .system_config import ConfigureSystemStep
from .target_config import ConfigureStorageStep


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
    ):
        self.log = log
        self.progress = progress
        self.status = status
        self.target = target
        self.runner = runner or CommandRunner(log)

    def run(self, plan: InstallPlan) -> InstallResult:
        context = InstallContext(plan, self.log)
        steps = [
            VerifyEnvironmentStep(self.runner),
            PrepareStorageStep(self.runner),
            MountTargetStep(self.runner, target=self.target),
            CopySystemStep(self.runner),
            ConfigureStorageStep(self.runner),
            EnterChrootStep(self.runner),
            CleanupLiveSystemStep(self.runner),
            ConfigureSystemStep(self.runner),
            SelectFastestAptMirrorStep(),
            # Establish the target-owned DKMS key before any package operation
            # can build kernel modules. Never let an upgrade inherit the
            # copied Live image's signing identity.
            PrepareSecureBootStep(self.runner),
        ]
        if plan.software.install_updates:
            steps.extend(
                (
                    RefreshPackageIndexesStep(self.runner),
                    UpgradeSystemStep(self.runner),
                )
            )
        if plan.software.install_third_party_drivers:
            steps.append(InstallThirdPartyDriversStep(self.runner))
        steps.extend(
            (
                VerifyDkmsSignaturesStep(self.runner),
                InstallBootloaderStep(self.runner),
                EnrollSecureBootStep(self.runner),
                LeaveChrootStep(self.runner),
                UnmountTargetStep(self.runner),
            )
        )
        return StepRunner(steps, self.progress, self.status).run(context)
