"""Versioned, serializable installation plan.

The plan describes desired state.  It deliberately contains no commands and
no plaintext secrets.  A privileged executor must validate it again against
the current machine before performing any destructive operation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .storage_graph import StorageGraph


SCHEMA_VERSION = 6


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
    GUIDED_COEXISTENCE = "guided-coexistence"
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
    graph: StorageGraph | None = None


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
        result = asdict(self)
        if self.storage.graph is not None:
            result["storage"]["graph"] = self.storage.graph.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InstallPlan":
        """Strictly parse the versioned untrusted privilege-boundary input."""
        root = _object(value, "plan")
        _exact_fields(
            root,
            {
                "schema_version",
                "source",
                "storage",
                "platform",
                "identity",
                "regional",
                "software",
                "swap",
                "boot",
            },
            "plan",
        )
        source_data = _object(root["source"], "source")
        _exact_fields(
            source_data,
            {"image_path", "manifest_path", "desktop_manifest_path"},
            "source",
        )
        source = SourceSpec(**source_data)

        storage_data = _object(root["storage"], "storage")
        _exact_fields(
            storage_data,
            {
                "mode",
                "disk",
                "filesystem",
                "esp_size_mib",
                "swap_size_mib",
                "graph",
            },
            "storage",
        )
        disk_data = _object(storage_data["disk"], "storage.disk")
        _exact_fields(
            disk_data,
            {"path", "stable_id", "expected_size_bytes", "model", "serial"},
            "storage.disk",
        )
        disk = DiskIdentity(**disk_data)
        storage = StorageSpec(
            **{
                **storage_data,
                "mode": InstallMode(storage_data["mode"]),
                "filesystem": Filesystem(storage_data["filesystem"]),
                "disk": disk,
                "graph": StorageGraph.from_dict(storage_data["graph"]),
            }
        )

        platform_data = _object(root["platform"], "platform")
        _exact_fields(
            platform_data,
            {"architecture", "firmware", "secure_boot"},
            "platform",
        )
        platform = PlatformSpec(
            architecture=Architecture(platform_data["architecture"]),
            firmware=Firmware(platform_data["firmware"]),
            secure_boot=SecureBoot(platform_data["secure_boot"]),
        )
        identity_data = _object(root["identity"], "identity")
        _exact_fields(
            identity_data,
            {
                "hostname",
                "username",
                "full_name",
                "authentication",
                "sudo_without_password",
                "password_hash",
            },
            "identity",
        )
        identity = IdentitySpec(
            **{
                **identity_data,
                "authentication": AuthenticationMode(
                    identity_data["authentication"]
                ),
            }
        )

        regional_data = _object(root["regional"], "regional")
        _exact_fields(
            regional_data,
            {"locale", "timezone", "keyboard", "input_method"},
            "regional",
        )
        keyboard_data = _object(regional_data["keyboard"], "regional.keyboard")
        _exact_fields(keyboard_data, {"layout", "variant"}, "regional.keyboard")
        keyboard = KeyboardSpec(**keyboard_data)
        regional = RegionalSpec(
            **{**regional_data, "keyboard": keyboard}
        )

        software_data = _object(root["software"], "software")
        _exact_fields(
            software_data,
            {"install_updates", "install_third_party_drivers"},
            "software",
        )
        software = SoftwareSpec(**software_data)
        swap_data = _object(root["swap"], "swap")
        _exact_fields(
            swap_data,
            {
                "zram_enabled",
                "zram_ram_percent",
                "zram_algorithm",
                "zram_priority",
                "disk_priority",
            },
            "swap",
        )
        swap = SwapSpec(**swap_data)
        boot_data = _object(root["boot"], "boot")
        _exact_fields(
            boot_data,
            {"install_fallback_path", "mok_password_policy"},
            "boot",
        )
        boot = BootSpec(
            **{
                **boot_data,
                "mok_password_policy": MokPasswordPolicy(
                    boot_data["mok_password_policy"]
                ),
            }
        )
        return cls(
            schema_version=root["schema_version"],
            source=source,
            storage=storage,
            platform=platform,
            identity=identity,
            regional=regional,
            software=software,
            swap=swap,
            boot=boot,
        )


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise TypeError(f"{path} must be an object")
    return value


def _exact_fields(
    value: dict[str, Any], expected: set[str], path: str
) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ValueError(f"Unknown field in {path}: {unknown[0]}")
    if missing:
        raise ValueError(f"Missing field in {path}: {missing[0]}")
