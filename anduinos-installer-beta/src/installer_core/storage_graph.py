"""Versioned, command-free storage graph schema.

The graph crosses the unprivileged-to-privileged boundary.  It therefore
contains stable identities, geometry authorization and typed declarations,
but never executable commands or raw formatter/mount arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


STORAGE_GRAPH_SCHEMA_VERSION = 5


class StorageGraphMode(str, Enum):
    ERASE_DISK = "erase-disk"
    GUIDED_COEXISTENCE = "guided-coexistence"
    MANUAL = "manual"


class BlockReferenceKind(str, Enum):
    DISK = "disk"
    PARTITION = "partition"
    FREE_EXTENT = "free-extent"
    MDRAID = "mdraid"
    LUKS = "luks"
    LVM = "lvm"
    BTRFS_FILESYSTEM = "btrfs-filesystem"


class GraphFilesystem(str, Enum):
    BTRFS = "btrfs"
    EXT4 = "ext4"
    XFS = "xfs"
    F2FS = "f2fs"
    VFAT = "vfat"
    SWAP = "swap"
    NTFS = "ntfs"


class StorageGraphAction(str, Enum):
    PRESERVE = "preserve"
    DELETE_PARTITION = "delete-partition"
    REPLACE_PARTITION_TABLE = "replace-partition-table"
    MODIFY_PARTITION_TABLE = "modify-partition-table"
    RESIZE_PARTITION = "resize-partition"
    CREATE_PARTITION = "create-partition"
    FORMAT = "format"
    CREATE_SUBVOLUME = "create-subvolume"
    CONFIGURE_MOUNTS = "configure-mounts"
    WRITE_BIOS_BOOTLOADER = "write-bios-bootloader"
    WRITE_BOOT_FILES = "write-boot-files"
    WRITE_FALLBACK_BOOT_FILES = "write-fallback-boot-files"
    UPDATE_NVRAM = "update-nvram"


class StorageCapability(str, Enum):
    BOOTABLE = "bootable"
    SYSTEM_ROLLBACK = "system-rollback"
    SNAPSHOT_MANAGEMENT = "snapshot-management"


class MountRole(str, Enum):
    ROOT = "root"
    EFI = "efi"
    HOME = "home"
    LOG = "log"
    SNAPSHOTS = "snapshots"
    CONTAINERS = "containers"
    VIRTUAL_MACHINES = "virtual-machines"


@dataclass(frozen=True)
class BlockReference:
    reference_id: str
    kind: BlockReferenceKind
    stable_id: str
    parent_reference_id: str
    expected_size_bytes: int
    start_bytes: int
    topology_digest: str


@dataclass(frozen=True)
class PartitionDeclaration:
    partition_id: str
    parent_reference_id: str
    number: int
    name: str
    start_mib: int
    end_mib: int | None
    flags: tuple[str, ...]


@dataclass(frozen=True)
class PartitionResizeDeclaration:
    """Authorized new size for one stable existing filesystem partition."""

    target_reference_id: str
    filesystem: GraphFilesystem
    original_size_bytes: int
    target_size_bytes: int


@dataclass(frozen=True)
class FilesystemDeclaration:
    filesystem_id: str
    block_id: str
    filesystem: GraphFilesystem
    label: str


@dataclass(frozen=True)
class SubvolumeDeclaration:
    subvolume_id: str
    filesystem_id: str
    name: str
    mount_point: str
    rollback_with_system: bool


@dataclass(frozen=True)
class MountDeclaration:
    source_id: str
    target_path: str
    role: MountRole


@dataclass(frozen=True)
class BootTarget:
    efi_filesystem_id: str
    bios_disk_reference_id: str
    vendor_directory: str
    fallback_path: str


@dataclass(frozen=True)
class StorageGraphOperation:
    action: StorageGraphAction
    target_id: str


@dataclass(frozen=True)
class StorageGraph:
    schema_version: int
    mode: StorageGraphMode
    inventory_digest: str
    partition_table: str
    block_references: tuple[BlockReference, ...]
    partitions: tuple[PartitionDeclaration, ...]
    partition_resizes: tuple[PartitionResizeDeclaration, ...]
    filesystems: tuple[FilesystemDeclaration, ...]
    subvolumes: tuple[SubvolumeDeclaration, ...]
    mounts: tuple[MountDeclaration, ...]
    boot_targets: tuple[BootTarget, ...]
    operations: tuple[StorageGraphOperation, ...]
    requested_capabilities: tuple[StorageCapability, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode.value,
            "inventory_digest": self.inventory_digest,
            "partition_table": self.partition_table,
            "block_references": [
                {
                    "reference_id": item.reference_id,
                    "kind": item.kind.value,
                    "stable_id": item.stable_id,
                    "parent_reference_id": item.parent_reference_id,
                    "expected_size_bytes": item.expected_size_bytes,
                    "start_bytes": item.start_bytes,
                    "topology_digest": item.topology_digest,
                }
                for item in self.block_references
            ],
            "partitions": [
                {
                    "partition_id": item.partition_id,
                    "parent_reference_id": item.parent_reference_id,
                    "number": item.number,
                    "name": item.name,
                    "start_mib": item.start_mib,
                    "end_mib": item.end_mib,
                    "flags": list(item.flags),
                }
                for item in self.partitions
            ],
            "partition_resizes": [
                {
                    "target_reference_id": item.target_reference_id,
                    "filesystem": item.filesystem.value,
                    "original_size_bytes": item.original_size_bytes,
                    "target_size_bytes": item.target_size_bytes,
                }
                for item in self.partition_resizes
            ],
            "filesystems": [
                {
                    "filesystem_id": item.filesystem_id,
                    "block_id": item.block_id,
                    "filesystem": item.filesystem.value,
                    "label": item.label,
                }
                for item in self.filesystems
            ],
            "subvolumes": [
                {
                    "subvolume_id": item.subvolume_id,
                    "filesystem_id": item.filesystem_id,
                    "name": item.name,
                    "mount_point": item.mount_point,
                    "rollback_with_system": item.rollback_with_system,
                }
                for item in self.subvolumes
            ],
            "mounts": [
                {
                    "source_id": item.source_id,
                    "target_path": item.target_path,
                    "role": item.role.value,
                }
                for item in self.mounts
            ],
            "boot_targets": [
                {
                    "efi_filesystem_id": item.efi_filesystem_id,
                    "bios_disk_reference_id": item.bios_disk_reference_id,
                    "vendor_directory": item.vendor_directory,
                    "fallback_path": item.fallback_path,
                }
                for item in self.boot_targets
            ],
            "operations": [
                {"action": item.action.value, "target_id": item.target_id}
                for item in self.operations
            ],
            "requested_capabilities": [
                item.value for item in self.requested_capabilities
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> "StorageGraph":
        graph = _mapping(value, "storage.graph")
        _exact_fields(
            graph,
            {
                "schema_version",
                "mode",
                "inventory_digest",
                "partition_table",
                "block_references",
                "partitions",
                "partition_resizes",
                "filesystems",
                "subvolumes",
                "mounts",
                "boot_targets",
                "operations",
                "requested_capabilities",
            },
            "storage.graph",
        )
        return cls(
            schema_version=_integer(
                graph["schema_version"], "storage.graph.schema_version"
            ),
            mode=StorageGraphMode(graph["mode"]),
            inventory_digest=_string(
                graph["inventory_digest"], "storage.graph.inventory_digest"
            ),
            partition_table=_string(
                graph["partition_table"], "storage.graph.partition_table"
            ),
            block_references=tuple(
                _block_reference(item, index)
                for index, item in enumerate(
                    _list(
                        graph["block_references"],
                        "storage.graph.block_references",
                    )
                )
            ),
            partitions=tuple(
                _partition(item, index)
                for index, item in enumerate(
                    _list(graph["partitions"], "storage.graph.partitions")
                )
            ),
            partition_resizes=tuple(
                _partition_resize(item, index)
                for index, item in enumerate(
                    _list(
                        graph["partition_resizes"],
                        "storage.graph.partition_resizes",
                    )
                )
            ),
            filesystems=tuple(
                _filesystem(item, index)
                for index, item in enumerate(
                    _list(graph["filesystems"], "storage.graph.filesystems")
                )
            ),
            subvolumes=tuple(
                _subvolume(item, index)
                for index, item in enumerate(
                    _list(graph["subvolumes"], "storage.graph.subvolumes")
                )
            ),
            mounts=tuple(
                _mount(item, index)
                for index, item in enumerate(
                    _list(graph["mounts"], "storage.graph.mounts")
                )
            ),
            boot_targets=tuple(
                _boot_target(item, index)
                for index, item in enumerate(
                    _list(
                        graph["boot_targets"],
                        "storage.graph.boot_targets",
                    )
                )
            ),
            operations=tuple(
                _operation(item, index)
                for index, item in enumerate(
                    _list(graph["operations"], "storage.graph.operations")
                )
            ),
            requested_capabilities=tuple(
                StorageCapability(item)
                for item in _list(
                    graph["requested_capabilities"],
                    "storage.graph.requested_capabilities",
                )
            ),
        )


def _block_reference(value: object, index: int) -> BlockReference:
    path = f"storage.graph.block_references[{index}]"
    item = _mapping(value, path)
    _exact_fields(
        item,
        {
            "reference_id",
            "kind",
            "stable_id",
            "parent_reference_id",
            "expected_size_bytes",
            "start_bytes",
            "topology_digest",
        },
        path,
    )
    return BlockReference(
        reference_id=_string(item["reference_id"], f"{path}.reference_id"),
        kind=BlockReferenceKind(item["kind"]),
        stable_id=_string(item["stable_id"], f"{path}.stable_id"),
        parent_reference_id=_string(
            item["parent_reference_id"], f"{path}.parent_reference_id"
        ),
        expected_size_bytes=_integer(
            item["expected_size_bytes"], f"{path}.expected_size_bytes"
        ),
        start_bytes=_integer(item["start_bytes"], f"{path}.start_bytes"),
        topology_digest=_string(
            item["topology_digest"], f"{path}.topology_digest"
        ),
    )


def _partition(value: object, index: int) -> PartitionDeclaration:
    path = f"storage.graph.partitions[{index}]"
    item = _mapping(value, path)
    _exact_fields(
        item,
        {
            "partition_id",
            "parent_reference_id",
            "number",
            "name",
            "start_mib",
            "end_mib",
            "flags",
        },
        path,
    )
    end = item["end_mib"]
    if end is not None:
        end = _integer(end, f"{path}.end_mib")
    return PartitionDeclaration(
        partition_id=_string(item["partition_id"], f"{path}.partition_id"),
        parent_reference_id=_string(
            item["parent_reference_id"], f"{path}.parent_reference_id"
        ),
        number=_integer(item["number"], f"{path}.number"),
        name=_string(item["name"], f"{path}.name"),
        start_mib=_integer(item["start_mib"], f"{path}.start_mib"),
        end_mib=end,
        flags=tuple(
            _string(flag, f"{path}.flags")
            for flag in _list(item["flags"], f"{path}.flags")
        ),
    )


def _filesystem(value: object, index: int) -> FilesystemDeclaration:
    path = f"storage.graph.filesystems[{index}]"
    item = _mapping(value, path)
    _exact_fields(
        item,
        {"filesystem_id", "block_id", "filesystem", "label"},
        path,
    )
    return FilesystemDeclaration(
        filesystem_id=_string(
            item["filesystem_id"], f"{path}.filesystem_id"
        ),
        block_id=_string(item["block_id"], f"{path}.block_id"),
        filesystem=GraphFilesystem(item["filesystem"]),
        label=_string(item["label"], f"{path}.label"),
    )


def _partition_resize(
    value: object,
    index: int,
) -> PartitionResizeDeclaration:
    path = f"storage.graph.partition_resizes[{index}]"
    item = _mapping(value, path)
    _exact_fields(
        item,
        {
            "target_reference_id",
            "filesystem",
            "original_size_bytes",
            "target_size_bytes",
        },
        path,
    )
    return PartitionResizeDeclaration(
        target_reference_id=_string(
            item["target_reference_id"], f"{path}.target_reference_id"
        ),
        filesystem=GraphFilesystem(item["filesystem"]),
        original_size_bytes=_integer(
            item["original_size_bytes"], f"{path}.original_size_bytes"
        ),
        target_size_bytes=_integer(
            item["target_size_bytes"], f"{path}.target_size_bytes"
        ),
    )


def _subvolume(value: object, index: int) -> SubvolumeDeclaration:
    path = f"storage.graph.subvolumes[{index}]"
    item = _mapping(value, path)
    _exact_fields(
        item,
        {
            "subvolume_id",
            "filesystem_id",
            "name",
            "mount_point",
            "rollback_with_system",
        },
        path,
    )
    rollback = item["rollback_with_system"]
    if type(rollback) is not bool:
        raise TypeError(f"{path}.rollback_with_system must be a boolean")
    return SubvolumeDeclaration(
        subvolume_id=_string(
            item["subvolume_id"], f"{path}.subvolume_id"
        ),
        filesystem_id=_string(
            item["filesystem_id"], f"{path}.filesystem_id"
        ),
        name=_string(item["name"], f"{path}.name"),
        mount_point=_string(
            item["mount_point"], f"{path}.mount_point"
        ),
        rollback_with_system=rollback,
    )


def _mount(value: object, index: int) -> MountDeclaration:
    path = f"storage.graph.mounts[{index}]"
    item = _mapping(value, path)
    _exact_fields(item, {"source_id", "target_path", "role"}, path)
    return MountDeclaration(
        source_id=_string(item["source_id"], f"{path}.source_id"),
        target_path=_string(item["target_path"], f"{path}.target_path"),
        role=MountRole(item["role"]),
    )


def _boot_target(value: object, index: int) -> BootTarget:
    path = f"storage.graph.boot_targets[{index}]"
    item = _mapping(value, path)
    _exact_fields(
        item,
        {
            "efi_filesystem_id",
            "bios_disk_reference_id",
            "vendor_directory",
            "fallback_path",
        },
        path,
    )
    return BootTarget(
        efi_filesystem_id=_string(
            item["efi_filesystem_id"], f"{path}.efi_filesystem_id"
        ),
        bios_disk_reference_id=_string(
            item["bios_disk_reference_id"],
            f"{path}.bios_disk_reference_id",
        ),
        vendor_directory=_string(
            item["vendor_directory"], f"{path}.vendor_directory"
        ),
        fallback_path=_string(
            item["fallback_path"], f"{path}.fallback_path"
        ),
    )


def _operation(value: object, index: int) -> StorageGraphOperation:
    path = f"storage.graph.operations[{index}]"
    item = _mapping(value, path)
    _exact_fields(item, {"action", "target_id"}, path)
    return StorageGraphOperation(
        action=StorageGraphAction(item["action"]),
        target_id=_string(item["target_id"], f"{path}.target_id"),
    )


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise TypeError(f"{path} must be an object")
    return value


def _list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{path} must be an array")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{path} must be a string")
    return value


def _integer(value: object, path: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{path} must be an integer")
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
