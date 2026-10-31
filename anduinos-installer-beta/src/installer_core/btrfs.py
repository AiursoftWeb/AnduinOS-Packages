"""Release-one Btrfs subvolume ABI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BtrfsCompression(str, Enum):
    NONE = "none"
    FAST = "fast"
    BALANCED = "balanced"
    SPACE = "space"

    @property
    def mount_option(self) -> str:
        return {
            BtrfsCompression.NONE: "compress=no",
            BtrfsCompression.FAST: "compress=zstd:1",
            BtrfsCompression.BALANCED: "compress=zstd:3",
            BtrfsCompression.SPACE: "compress=zstd:6",
        }[self]


@dataclass(frozen=True)
class BtrfsSubvolume:
    name: str
    mount_point: str
    rollback_with_system: bool

    def mount_options(
        self, compression: BtrfsCompression = BtrfsCompression.BALANCED,
    ) -> str:
        return f"defaults,subvol={self.name},{compression.mount_option},noatime"


BTRFS_SUBVOLUMES = (
    BtrfsSubvolume("@root", "/", True),
    BtrfsSubvolume("@home", "/home", False),
    BtrfsSubvolume("@log", "/var/log", False),
    BtrfsSubvolume("@snapshots", "/.snapshots", False),
    BtrfsSubvolume("@containers", "/var/lib/containers", False),
    BtrfsSubvolume("@libvirt", "/var/lib/libvirt/images", False),
)

