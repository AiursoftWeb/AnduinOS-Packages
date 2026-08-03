"""Declarative installation core for the AnduinOS installer."""

from .layout import PartitionLayout, PartitionSpec, build_erase_disk_layout
from .model import (
    Architecture,
    AuthenticationMode,
    Filesystem,
    Firmware,
    InstallMode,
    InstallPlan,
    MokPasswordPolicy,
    SecureBoot,
)
from .steps import (
    FailurePolicy,
    InstallContext,
    InstallResult,
    InstallStep,
    StepResult,
    StepRunner,
)
from .validation import PlanValidationError, validate_plan

__all__ = [
    "Architecture",
    "AuthenticationMode",
    "FailurePolicy",
    "Filesystem",
    "Firmware",
    "InstallContext",
    "InstallMode",
    "InstallPlan",
    "InstallResult",
    "InstallStep",
    "MokPasswordPolicy",
    "PartitionLayout",
    "PartitionSpec",
    "PlanValidationError",
    "SecureBoot",
    "StepResult",
    "StepRunner",
    "build_erase_disk_layout",
    "validate_plan",
]
"""Public API for the declarative AnduinOS installer core."""

from .layout import build_erase_disk_layout
from .planning import build_plan
from .probe import PlatformProbe, ProbeError, probe_disks, probe_platform
from .storage_commands import build_storage_commands
from .target_config import ConfigureStorageStep
from .executor import InstallerExecutor
from .system_config import ConfigureSystemStep
from .chroot_env import EnterChrootStep, LeaveChrootStep
from .live_cleanup import CleanupLiveSystemStep
from .language_support import InstallLanguagePacksStep
from .boot_commands import build_boot_commands
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
from .mirrors import SelectFastestAptMirrorStep
from .validation import PlanValidationError, validate_plan

__all__ = [
    "PlanValidationError",
    "PlatformProbe",
    "ProbeError",
    "build_erase_disk_layout",
    "build_plan",
    "build_storage_commands",
    "ConfigureStorageStep",
    "InstallerExecutor",
    "ConfigureSystemStep",
    "EnterChrootStep",
    "LeaveChrootStep",
    "CleanupLiveSystemStep",
    "InstallLanguagePacksStep",
    "build_boot_commands",
    "InstallBootloaderStep",
    "PrepareSecureBootStep",
    "EnrollSecureBootStep",
    "InstallThirdPartyDriversStep",
    "RefreshPackageIndexesStep",
    "UpgradeSystemStep",
    "VerifyDkmsSignaturesStep",
    "SelectFastestAptMirrorStep",
    "probe_disks",
    "probe_platform",
    "validate_plan",
]
