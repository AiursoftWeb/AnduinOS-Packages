"""Versioned, serializable installation plan.

The plan describes desired state.  It deliberately contains no commands and
no plaintext secrets.  A privileged executor must validate it again against
the current machine before performing any destructive operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


SCHEMA_VERSION = 4


class Architecture(str, Enum):
    AMD64 = "amd64"
    ARM64 = "arm64"


class Firmware(str, Enum):
    UEFI = "uefi"
    BIOS = "bios"


class SecureBoot(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    NOT_APPLICABLE = "not-applicable"


class InstallMode(str, Enum):
    ERASE_DISK = "erase-disk"
    MANUAL = "manual"


class Filesystem(str, Enum):
    BTRFS = "btrfs"
    EXT4 = "ext4"


class MokPasswordPolicy(str, Enum):
    ANDUINOS_DEFAULT = "anduinos-default"
    NOT_APPLICABLE = "not-applicable"


class AuthenticationMode(str, Enum):
    PASSWORD = "password"
    PASSWORDLESS_SHARED = "passwordless-shared"


@dataclass(frozen=True)
class SourceSpec:
    image_path: str = "/cdrom/casper/filesystem.squashfs"
    manifest_path: str = "/cdrom/casper/filesystem.manifest"
    desktop_manifest_path: str = "/cdrom/casper/filesystem.manifest-desktop"


@dataclass(frozen=True)
class DiskIdentity:
    path: str
    stable_id: str
    expected_size_bytes: int
    model: str = ""
    serial: str = ""


@dataclass(frozen=True)
class StorageSpec:
    mode: InstallMode
    disk: DiskIdentity
    filesystem: Filesystem = Filesystem.BTRFS
    esp_size_mib: int = 1024
    swap_size_mib: int = 4096


@dataclass(frozen=True)
class PlatformSpec:
    architecture: Architecture
    firmware: Firmware
    secure_boot: SecureBoot


@dataclass(frozen=True)
class IdentitySpec:
    hostname: str
    username: str
    full_name: str
    authentication: AuthenticationMode = AuthenticationMode.PASSWORD
    sudo_without_password: bool = False
    # A crypt-compatible hash, never a plaintext password.
    password_hash: str = field(default="", repr=False)


@dataclass(frozen=True)
class KeyboardSpec:
    layout: str
    variant: str = ""


@dataclass(frozen=True)
class RegionalSpec:
    locale: str
    timezone: str
    keyboard: KeyboardSpec
    input_method: str | None = None


@dataclass(frozen=True)
class SwapSpec:
    zram_enabled: bool = True
    zram_ram_percent: int = 50
    zram_algorithm: str = "lz4"
    zram_priority: int = 100
    disk_priority: int = 10


@dataclass(frozen=True)
class BootSpec:
    install_fallback_path: bool = True
    mok_password_policy: MokPasswordPolicy = MokPasswordPolicy.NOT_APPLICABLE


@dataclass(frozen=True)
class SoftwareSpec:
    install_updates: bool = True
    install_third_party_drivers: bool = False


@dataclass(frozen=True)
class InstallPlan:
    schema_version: int
    source: SourceSpec
    storage: StorageSpec
    platform: PlatformSpec
    identity: IdentitySpec
    regional: RegionalSpec
    software: SoftwareSpec = field(default_factory=SoftwareSpec)
    swap: SwapSpec = field(default_factory=SwapSpec)
    boot: BootSpec = field(default_factory=BootSpec)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON/YAML-safe mapping."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InstallPlan":
        """Parse a mapping while rejecting unknown enum values."""
        source = SourceSpec(**value["source"])
        disk = DiskIdentity(**value["storage"]["disk"])
        storage = StorageSpec(
            **{
                **value["storage"],
                "mode": InstallMode(value["storage"]["mode"]),
                "filesystem": Filesystem(value["storage"]["filesystem"]),
                "disk": disk,
            }
        )
        platform = PlatformSpec(
            architecture=Architecture(value["platform"]["architecture"]),
            firmware=Firmware(value["platform"]["firmware"]),
            secure_boot=SecureBoot(value["platform"]["secure_boot"]),
        )
        identity_data = value["identity"]
        identity = IdentitySpec(
            **{
                **identity_data,
                "authentication": AuthenticationMode(
                    identity_data.get(
                        "authentication", AuthenticationMode.PASSWORD.value
                    )
                ),
            }
        )
        keyboard = KeyboardSpec(**value["regional"]["keyboard"])
        regional = RegionalSpec(
            **{**value["regional"], "keyboard": keyboard}
        )
        software = SoftwareSpec(**value.get("software", {}))
        swap = SwapSpec(**value.get("swap", {}))
        boot_data = value.get("boot", {})
        boot = BootSpec(
            **{
                **boot_data,
                "mok_password_policy": MokPasswordPolicy(
                    boot_data.get(
                        "mok_password_policy",
                        MokPasswordPolicy.NOT_APPLICABLE.value,
                    )
                ),
            }
        )
        return cls(
            schema_version=value["schema_version"],
            source=source,
            storage=storage,
            platform=platform,
            identity=identity,
            regional=regional,
            software=software,
            swap=swap,
            boot=boot,
        )
