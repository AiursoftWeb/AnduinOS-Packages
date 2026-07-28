from installer_core.model import (
    Architecture,
    BootSpec,
    DiskIdentity,
    Filesystem,
    Firmware,
    IdentitySpec,
    InstallMode,
    InstallPlan,
    KeyboardSpec,
    MokPasswordPolicy,
    PlatformSpec,
    RegionalSpec,
    SCHEMA_VERSION,
    SecureBoot,
    SourceSpec,
    StorageSpec,
    SwapSpec,
)


def valid_plan(
    *,
    architecture: Architecture = Architecture.AMD64,
    firmware: Firmware = Firmware.UEFI,
    secure_boot: SecureBoot = SecureBoot.ENABLED,
    filesystem: Filesystem = Filesystem.BTRFS,
) -> InstallPlan:
    mok_policy = (
        MokPasswordPolicy.ANDUINOS_DEFAULT
        if secure_boot is SecureBoot.ENABLED
        else MokPasswordPolicy.NOT_APPLICABLE
    )
    return InstallPlan(
        schema_version=SCHEMA_VERSION,
        source=SourceSpec(),
        storage=StorageSpec(
            mode=InstallMode.ERASE_DISK,
            disk=DiskIdentity(
                path="/dev/nvme0n1",
                stable_id="nvme-Samsung_SSD-test",
                expected_size_bytes=128 * 1024**3,
                model="Samsung Test SSD",
                serial="TEST123",
            ),
            filesystem=filesystem,
        ),
        platform=PlatformSpec(
            architecture=architecture,
            firmware=firmware,
            secure_boot=secure_boot,
        ),
        identity=IdentitySpec(
            hostname="anduinos",
            username="alice",
            full_name="Alice Example",
            password_hash="$y$j9T$example$example",
        ),
        regional=RegionalSpec(
            locale="en_US.UTF-8",
            timezone="Asia/Singapore",
            keyboard=KeyboardSpec(layout="us"),
        ),
        swap=SwapSpec(),
        boot=BootSpec(mok_password_policy=mok_policy),
    )
