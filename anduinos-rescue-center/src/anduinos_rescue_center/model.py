"""Immutable data exchanged between the rescue helper and GTK client."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Partition:
    path: str
    parent: str
    size_bytes: int
    filesystem: str
    filesystem_uuid: str
    label: str
    partuuid: str
    mountpoints: tuple[str, ...]
    identity: str
    active_system: bool = False
    os_kind: str = "unknown"
    os_name: str = ""
    os_version: str = ""
    hostname: str = ""
    btrfs_layout: bool = False
    probe_error: str = ""

    @property
    def is_anduinos(self) -> bool:
        return self.os_kind == "anduinos"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["mountpoints"] = list(self.mountpoints)
        return value


@dataclass(frozen=True)
class Disk:
    path: str
    model: str
    serial: str
    size_bytes: int
    transport: str
    removable: bool
    read_only: bool
    live_medium: bool
    identity: str
    partitions: tuple[Partition, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["partitions"] = [partition.to_dict() for partition in self.partitions]
        return value


@dataclass(frozen=True)
class Inventory:
    disks: tuple[Disk, ...]
    live: bool
    secure_boot: str

    @property
    def installations(self) -> tuple[Partition, ...]:
        return tuple(
            partition
            for disk in self.disks
            for partition in disk.partitions
            if partition.is_anduinos
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "live": self.live,
            "secure_boot": self.secure_boot,
            "disks": [disk.to_dict() for disk in self.disks],
        }
