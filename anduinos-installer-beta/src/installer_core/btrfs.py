"""Release-one Btrfs subvolume ABI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BtrfsSubvolume:
    name: str
    mount_point: str
    rollback_with_system: bool

    @property
    def mount_options(self) -> str:
        return f"defaults,subvol={self.name},compress=zstd,noatime"


BTRFS_SUBVOLUMES = (
    BtrfsSubvolume("@root", "/", True),
    BtrfsSubvolume("@home", "/home", False),
    BtrfsSubvolume("@log", "/var/log", False),
    BtrfsSubvolume("@snapshots", "/.snapshots", False),
    BtrfsSubvolume("@containers", "/var/lib/containers", False),
    BtrfsSubvolume("@libvirt", "/var/lib/libvirt/images", False),
)

