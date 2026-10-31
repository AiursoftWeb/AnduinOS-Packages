"""Rules for offering offline recovery targets in the installation picker."""

from __future__ import annotations


SUPPORTED_FILESYSTEMS = frozenset({"btrfs", "ext2", "ext3", "ext4", "xfs"})


def can_open_partition(disk: dict, partition: dict) -> bool:
    """Never offer the running system, Live media, or an unbound device."""
    return bool(
        partition.get("path")
        and partition.get("identity")
        and not partition.get("active_system")
        and not disk.get("live_medium")
        and str(partition.get("filesystem") or "").lower() in SUPPORTED_FILESYSTEMS
        and partition.get("os_kind") != "windows"
    )
