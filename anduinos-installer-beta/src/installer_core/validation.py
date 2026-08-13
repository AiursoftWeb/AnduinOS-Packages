"""Pure validation for untrusted installation plans."""

from __future__ import annotations

import re
from enum import Enum

from languages import input_method, language_for_locale

from .model import (
    AuthenticationMode,
    Architecture,
    Firmware,
    InstallMode,
    InstallPlan,
    MokPasswordPolicy,
    SCHEMA_VERSION,
    SecureBoot,
)
from .hostnames import is_canonical_hostname
from .storage_graph_planning import validate_storage_graph
from .swap_policy import MINIMUM_DISK_SWAP_MIB, MINIMUM_ROOT_MIB
from .username_policy import RESERVED_USERNAMES, is_valid_username


MINIMUM_DISK_BYTES = 24 * 1024**3
MINIMUM_ROOT_BYTES = MINIMUM_ROOT_MIB * 1024**2
LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:_[A-Z]{2})?\.UTF-8$")
TIMEZONE_RE = re.compile(
    r"^(?:UTC|[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+)$"
)
WHOLE_DISK_RE = re.compile(
    r"^/dev/(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$"
)


class PlanValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))


class ExecutionPolicy(str, Enum):
    """Executor-owned capability; it is never serialized in an install plan."""

    RELEASE = "release"
    GUIDED_DESTRUCTIVE_TEST = "guided-destructive-test"


def validate_plan_for_execution(
    plan: InstallPlan,
    policy: ExecutionPolicy = ExecutionPolicy.RELEASE,
) -> None:
    """Validate against a capability selected by the privileged process."""

    if not isinstance(policy, ExecutionPolicy):
        raise PlanValidationError(["Invalid executor policy"])
    validate_plan(
        plan,
        allow_guided_compilation=True,
    )
    if (
        policy is ExecutionPolicy.RELEASE
        and plan.identity.authentication
        is AuthenticationMode.PASSWORDLESS_SHARED
    ):
        raise PlanValidationError(
            ["Release installation requires a password-protected account"]
        )


def validate_plan(
    plan: InstallPlan,
    *,
    allow_guided_compilation: bool = True,
) -> None:
    """Validate an erase-disk or beta guided-coexistence plan."""

    errors: list[str] = []

    if plan.schema_version != SCHEMA_VERSION:
        errors.append(
            f"Unsupported schema version {plan.schema_version}; "
            f"expected {SCHEMA_VERSION}"
        )

    if (
        plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
        and not allow_guided_compilation
    ):
        errors.append("Guided coexistence execution is not enabled")
    elif plan.storage.mode not in {
        InstallMode.ERASE_DISK,
        InstallMode.GUIDED_COEXISTENCE,
    }:
        errors.append("Manual partitioning is not implemented")

    disk = plan.storage.disk
    if not WHOLE_DISK_RE.fullmatch(disk.path):
        errors.append("Target must be a supported whole-disk device")
    if not disk.stable_id.strip():
        errors.append("Target disk requires a stable hardware identifier")
    if disk.expected_size_bytes < MINIMUM_DISK_BYTES:
        errors.append("Target disk must be at least 24 GiB")

    reserved = (
        plan.storage.esp_size_mib + plan.storage.swap_size_mib + 4
    ) * 1024**2
    if disk.expected_size_bytes - reserved < MINIMUM_ROOT_BYTES:
        errors.append("Partition layout leaves less than 20 GiB for root")
    if plan.storage.esp_size_mib < 512:
        errors.append("EFI System Partition must be at least 512 MiB")
    if (
        type(plan.storage.swap_size_mib) is not int
        or plan.storage.swap_size_mib < MINIMUM_DISK_SWAP_MIB
        or plan.storage.swap_size_mib % 1024
    ):
        errors.append(
            "Disk swap must be at least 2 GiB and use whole-GiB sizing"
        )
    try:
        validate_storage_graph(plan)
    except ValueError as error:
        errors.append(str(error))

    platform = plan.platform
    if platform.architecture is Architecture.ARM64:
        if platform.firmware is not Firmware.UEFI:
            errors.append("arm64 supports standards-based UEFI only")
    if platform.firmware is Firmware.BIOS:
        if platform.architecture is not Architecture.AMD64:
            errors.append("Legacy BIOS is supported on amd64 only")
        if platform.secure_boot is not SecureBoot.NOT_APPLICABLE:
            errors.append("Secure Boot is not applicable to Legacy BIOS")
    if platform.firmware is Firmware.UEFI:
        if platform.secure_boot is SecureBoot.NOT_APPLICABLE:
            errors.append("UEFI plan must declare the Secure Boot state")

    expected_mok = (
        MokPasswordPolicy.ANDUINOS_DEFAULT
        if platform.secure_boot is SecureBoot.ENABLED
        else MokPasswordPolicy.NOT_APPLICABLE
    )
    if plan.boot.mok_password_policy is not expected_mok:
        errors.append("MOK password policy does not match Secure Boot state")
    if type(plan.boot.install_fallback_path) is not bool:
        errors.append("EFI fallback-path policy must be boolean")
    elif (
        plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
        and plan.boot.install_fallback_path
    ):
        errors.append(
            "Install alongside must not write the shared EFI fallback path"
        )

    identity = plan.identity
    if identity.username in RESERVED_USERNAMES:
        errors.append("Reserved username")
    elif not is_valid_username(identity.username):
        errors.append("Invalid username")
    if not is_canonical_hostname(identity.hostname):
        errors.append("Invalid hostname")
    if (
        not identity.full_name.strip()
        or len(identity.full_name) > 128
        or any(character in identity.full_name for character in ":\r\n")
    ):
        errors.append("Invalid full name")
    if identity.authentication is AuthenticationMode.PASSWORD:
        if (
            not identity.password_hash.startswith(("$y$", "$6$"))
            or len(identity.password_hash) > 512
            or not re.fullmatch(r"[!-~]+", identity.password_hash)
            or ":" in identity.password_hash
        ):
            errors.append("Password must be a yescrypt or SHA-512 crypt hash")
    elif identity.authentication is AuthenticationMode.PASSWORDLESS_SHARED:
        if identity.password_hash:
            errors.append("Passwordless shared account must not carry a password")
    else:
        errors.append("Invalid account authentication mode")
    access = plan.access
    if type(access.sudo_without_password) is not bool:
        errors.append("Passwordless sudo policy must be boolean")
    if type(access.automatic_login) is not bool:
        errors.append("Automatic-login policy must be boolean")
    if type(access.ssh_password_login) is not bool:
        errors.append("SSH password-login policy must be boolean")
    if (
        identity.authentication is AuthenticationMode.PASSWORDLESS_SHARED
        and access.sudo_without_password is not True
    ):
        errors.append("Passwordless shared account requires passwordless sudo")
    if (
        identity.authentication is AuthenticationMode.PASSWORDLESS_SHARED
        and access.automatic_login is not True
    ):
        errors.append("Passwordless shared account requires automatic login")
    if (
        identity.authentication is AuthenticationMode.PASSWORDLESS_SHARED
        and access.ssh_password_login
    ):
        errors.append("SSH password login requires a password-protected account")

    regional = plan.regional
    if not LOCALE_RE.fullmatch(regional.locale):
        errors.append("Invalid UTF-8 locale")
    if not TIMEZONE_RE.fullmatch(regional.timezone):
        errors.append("Invalid timezone")
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", regional.keyboard.layout):
        errors.append("Invalid keyboard layout")
    configured_language = language_for_locale(regional.locale)
    if configured_language is None:
        errors.append("Unsupported installer locale")
    selected_input_methods: tuple[str, ...] = ()
    if type(regional.input_methods) is not tuple:
        errors.append("Input methods must be an ordered tuple")
    elif not all(
        isinstance(method_id, str) for method_id in regional.input_methods
    ):
        errors.append("Input method identifiers must be strings")
    else:
        selected_input_methods = regional.input_methods
        if len(selected_input_methods) != len(set(selected_input_methods)):
            errors.append("Input methods must not contain duplicates")
    if configured_language is not None:
        maintained = configured_language.recommended_input_methods
        if any(
            method_id not in maintained
            for method_id in selected_input_methods
        ):
            errors.append(
                "Input methods do not match installer language policy"
            )
        selected_set = set(selected_input_methods)
        policy_order = tuple(
            method_id for method_id in maintained if method_id in selected_set
        )
        if selected_input_methods != policy_order:
            errors.append("Input methods must follow language policy order")
    if any(
        input_method(method_id) is None
        for method_id in selected_input_methods
    ):
        errors.append("Unknown input method")

    swap = plan.swap
    if not swap.zram_enabled:
        errors.append("Release plan requires zram to be enabled")
    if swap.zram_ram_percent != 50:
        errors.append("Release plan requires zram at 50% of RAM")
    if swap.zram_algorithm != "lz4":
        errors.append("Release plan requires lz4 zram")
    if swap.disk_priority >= swap.zram_priority:
        errors.append("Disk swap priority must be lower than zram priority")

    if type(plan.software.install_updates) is not bool:
        errors.append("Install-updates policy must be boolean")
    if type(plan.software.install_third_party_drivers) is not bool:
        errors.append("Third-party-driver policy must be boolean")
    if type(plan.software.install_multimedia_codecs) is not bool:
        errors.append("Multimedia-codec policy must be boolean")

    if errors:
        raise PlanValidationError(errors)
