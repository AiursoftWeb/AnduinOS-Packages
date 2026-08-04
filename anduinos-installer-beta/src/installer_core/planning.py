"""Convert unprivileged wizard choices into a validated installation plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from languages import DEFAULT_LOCALE, language_for_locale

from .model import (
    BootSpec,
    AuthenticationMode,
    Filesystem,
    IdentitySpec,
    InstallMode,
    InstallPlan,
    KeyboardSpec,
    MokPasswordPolicy,
    PlatformSpec,
    RegionalSpec,
    SCHEMA_VERSION,
    SoftwareSpec,
    SourceSpec,
    StorageSpec,
)
from .probe import PlatformProbe
from .storage_graph_planning import build_erase_disk_storage_graph
from .storage_inventory import DiskTopologyBinding
from .swap_policy import (
    calculate_swap_sizing,
    probe_physical_memory_bytes,
)
from .validation import validate_plan
from .model import DiskIdentity, SecureBoot


def build_plan(
    choices: Mapping[str, object],
    disk: DiskIdentity,
    platform: PlatformProbe,
    password_hash: str,
    *,
    disk_binding: DiskTopologyBinding,
    inventory_digest: str,
    physical_memory_probe=probe_physical_memory_bytes,
) -> InstallPlan:
    locale = str(choices.get("locale") or DEFAULT_LOCALE)
    language = language_for_locale(locale)
    if language is None:
        raise ValueError(f"Unsupported installer locale: {locale}")
    swap_sizing = calculate_swap_sizing(
        physical_memory_probe(),
        disk.expected_size_bytes,
    )
    plan = InstallPlan(
        schema_version=SCHEMA_VERSION,
        source=SourceSpec(),
        storage=StorageSpec(
            mode=InstallMode.ERASE_DISK,
            disk=disk,
            filesystem=Filesystem(str(choices.get("filesystem") or "btrfs")),
            swap_size_mib=swap_sizing.swap_size_mib,
        ),
        platform=PlatformSpec(
            architecture=platform.architecture,
            firmware=platform.firmware,
            secure_boot=platform.secure_boot,
        ),
        identity=IdentitySpec(
            hostname=str(choices.get("hostname") or ""),
            username=str(choices.get("username") or ""),
            full_name=str(choices.get("full_name") or ""),
            authentication=(
                AuthenticationMode.PASSWORDLESS_SHARED
                if choices.get("passwordless_shared")
                else AuthenticationMode.PASSWORD
            ),
            sudo_without_password=bool(
                choices.get("sudo_without_password", False)
            ),
            password_hash=password_hash,
        ),
        regional=RegionalSpec(
            locale=locale,
            timezone=str(choices.get("timezone") or ""),
            keyboard=KeyboardSpec(str(choices.get("keyboard") or "")),
            input_method=(
                language.recommended_input_method
                if bool(
                    choices.get(
                        "install_input_method",
                        language.recommended_input_method is not None,
                    )
                )
                else None
            ),
        ),
        software=SoftwareSpec(
            install_updates=bool(choices.get("install_updates", True)),
            install_third_party_drivers=bool(
                choices.get("install_third_party_drivers", False)
            ),
        ),
        boot=BootSpec(
            mok_password_policy=(
                MokPasswordPolicy.ANDUINOS_DEFAULT
                if platform.secure_boot is SecureBoot.ENABLED
                else MokPasswordPolicy.NOT_APPLICABLE
            )
        ),
    )
    graph = build_erase_disk_storage_graph(
        plan,
        disk_binding,
        inventory_digest,
    )
    plan = replace(plan, storage=replace(plan.storage, graph=graph))
    validate_plan(plan)
    return plan
