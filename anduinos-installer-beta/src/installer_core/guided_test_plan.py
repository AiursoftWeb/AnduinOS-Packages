"""Pure builder for passwordless guided plans used only by VM campaigns."""

from __future__ import annotations

from dataclasses import replace

from .model import (
    SCHEMA_VERSION,
    AuthenticationMode,
    BootSpec,
    IdentitySpec,
    InstallPlan,
    KeyboardSpec,
    MokPasswordPolicy,
    PlatformSpec,
    RegionalSpec,
    SecureBoot,
    SoftwareSpec,
    SourceSpec,
)
from .storage_ui import (
    GuidedStorageSelection,
    StorageWorkflow,
    build_guided_storage_preview,
)
from .validation import (
    ExecutionPolicy,
    validate_plan_for_execution,
)


def build_guided_vm_test_plan(
    workflow: StorageWorkflow,
    selection: GuidedStorageSelection,
    *,
    source_image: str = "/cdrom/casper/filesystem.squashfs",
    username: str = "anduinostest",
    full_name: str = "AnduinOS VM Test",
    hostname: str = "anduinos-test",
    locale: str = "en_US.UTF-8",
    timezone: str = "Etc/UTC",
    keyboard: str = "us",
) -> InstallPlan:
    preview = build_guided_storage_preview(workflow, selection)
    platform = workflow.platform
    draft = InstallPlan(
        schema_version=SCHEMA_VERSION,
        source=SourceSpec(image_path=source_image),
        storage=replace(
            _preview_storage(preview),
            graph=preview.graph,
        ),
        platform=PlatformSpec(
            architecture=platform.architecture,
            firmware=platform.firmware,
            secure_boot=platform.secure_boot,
        ),
        identity=IdentitySpec(
            hostname=hostname,
            username=username,
            full_name=full_name,
            authentication=AuthenticationMode.PASSWORDLESS_SHARED,
            sudo_without_password=True,
        ),
        regional=RegionalSpec(
            locale=locale,
            timezone=timezone,
            keyboard=KeyboardSpec(keyboard),
        ),
        software=SoftwareSpec(
            install_updates=False,
            install_third_party_drivers=False,
        ),
        boot=BootSpec(
            install_fallback_path=False,
            mok_password_policy=(
                MokPasswordPolicy.ANDUINOS_DEFAULT
                if platform.secure_boot is SecureBoot.ENABLED
                else MokPasswordPolicy.NOT_APPLICABLE
            ),
        ),
    )
    validate_plan_for_execution(
        draft, ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST
    )
    return draft


def _preview_storage(preview):
    from .model import InstallMode, StorageSpec

    return StorageSpec(
        mode=InstallMode.GUIDED_COEXISTENCE,
        disk=preview.disk.identity,
        filesystem=preview.selection.filesystem,
    )
