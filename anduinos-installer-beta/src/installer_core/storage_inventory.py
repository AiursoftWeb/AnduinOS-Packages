"""Immutable, read-only storage topology discovery.

This module is deliberately independent from the release-one ``InstallPlan``.
It introduces the stronger identities and geometry needed by future guided
and custom storage modes without changing the existing destructive path.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .model import DiskIdentity
from .probe import (
    ProbeError,
    SUPPORTED_WHOLE_DISK_RE,
    _stable_disk_id,
)


EFI_SYSTEM_PARTITION_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
MICROSOFT_RESERVED_PARTITION_GUID = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
MICROSOFT_BASIC_DATA_PARTITION_GUID = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
WINDOWS_RECOVERY_PARTITION_GUID = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"


@dataclass(frozen=True)
class PartitionIdentity:
    """Stable partition identity plus immutable on-disk geometry."""

    path: str
    number: int
    partuuid: str
    start_bytes: int
    size_bytes: int

    @property
    def end_bytes(self) -> int:
        return self.start_bytes + self.size_bytes - 1


@dataclass(frozen=True)
class PartitionInventory:
    """A directly partitioned child of one physical/logical disk."""

    identity: PartitionIdentity
    parent_disk_id: str
    partition_type: str = ""
    filesystem_type: str = ""
    filesystem_uuid: str = ""
    filesystem_label: str = ""
    mountpoints: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()

    @property
    def is_efi_system_partition(self) -> bool:
        parttype = self.partition_type.strip("{}").lower()
        return parttype == EFI_SYSTEM_PARTITION_GUID or "esp" in self.flags

    @property
    def is_efi_filesystem_candidate(self) -> bool:
        """Return basic type compatibility, not final reuse authorization."""

        return self.is_efi_system_partition and self.filesystem_type.lower() in {
            "fat",
            "fat16",
            "fat32",
            "vfat",
        }

    @property
    def is_windows_partition(self) -> bool:
        parttype = self.partition_type.strip("{}").lower()
        return parttype in {
            MICROSOFT_RESERVED_PARTITION_GUID,
            MICROSOFT_BASIC_DATA_PARTITION_GUID,
            WINDOWS_RECOVERY_PARTITION_GUID,
        } or self.filesystem_type.lower() in {"ntfs", "bitlocker"}

    @property
    def is_windows_critical_partition(self) -> bool:
        parttype = self.partition_type.strip("{}").lower()
        return self.is_efi_system_partition or parttype in {
            MICROSOFT_RESERVED_PARTITION_GUID,
            WINDOWS_RECOVERY_PARTITION_GUID,
        }

    @property
    def is_bitlocker_partition(self) -> bool:
        return self.filesystem_type.lower() == "bitlocker"


@dataclass(frozen=True)
class FreeExtent:
    """Unallocated geometry reported by the current partition table."""

    parent_disk_id: str
    start_bytes: int
    size_bytes: int

    @property
    def end_bytes(self) -> int:
        return self.start_bytes + self.size_bytes - 1

    @property
    def extent_id(self) -> str:
        value = (
            f"{self.parent_disk_id}:{self.start_bytes}:{self.size_bytes}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DiskInventory:
    """One fixed disk and the topology visible directly below it."""

    identity: DiskIdentity
    partition_table: str
    partition_table_uuid: str
    partitions: tuple[PartitionInventory, ...]
    free_extents: tuple[FreeExtent, ...]
    topology_digest: str
    geometry_probe_error: str = ""
    unsupported_descendant_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class StorageInventory:
    """A deterministic snapshot of every supported fixed disk."""

    disks: tuple[DiskInventory, ...]
    digest: str

    def disk(self, stable_id: str) -> DiskInventory:
        for item in self.disks:
            if item.identity.stable_id == stable_id:
                return item
        raise KeyError(stable_id)


@dataclass(frozen=True)
class DiskTopologyBinding:
    """The minimum topology authorization carried by a future plan."""

    stable_id: str
    expected_size_bytes: int
    topology_digest: str


class StaleStorageInventoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _PartedPartition:
    number: int
    start_bytes: int
    size_bytes: int
    flags: tuple[str, ...]


@dataclass(frozen=True)
class _PartedGeometry:
    partitions: tuple[_PartedPartition, ...]
    free_extents: tuple[tuple[int, int], ...]
    error: str = ""


def probe_storage_inventory(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> StorageInventory:
    """Probe fixed disks, their direct partitions and unallocated extents.

    ``lsblk`` provides the block hierarchy and persistent filesystem fields.
    ``parted print free`` is read-only and provides exact byte geometry for
    allocated and free regions. A disk with no readable partition table is
    still returned so release-one whole-disk erase can remain available, but
    it has no selectable free extents.
    """

    command = [
        "lsblk",
        "--json",
        "--bytes",
        "--paths",
        "--output",
        (
            "PATH,SIZE,MODEL,SERIAL,WWN,TYPE,RM,MAJ:MIN,LOG-SEC,"
            "PTTYPE,PTUUID,PARTUUID,PARTTYPE,PARTN,START,FSTYPE,UUID,"
            "LABEL,MOUNTPOINTS"
        ),
    ]
    try:
        result = run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ProbeError(f"Cannot enumerate storage topology: {error}") from error
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or "lsblk failed")

    try:
        roots = json.loads(result.stdout)["blockdevices"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ProbeError("lsblk returned invalid storage JSON") from error
    if not isinstance(roots, list):
        raise ProbeError("lsblk returned invalid block device data")

    disks: list[DiskInventory] = []
    for root in roots:
        if not isinstance(root, dict):
            continue
        if str(root.get("type") or "") != "disk" or _as_bool(root.get("rm")):
            continue
        path = str(root.get("path") or "")
        if not SUPPORTED_WHOLE_DISK_RE.fullmatch(path):
            continue
        stable_id = _stable_disk_id(
            path,
            str(root.get("wwn") or ""),
            str(root.get("serial") or ""),
            str(root.get("maj:min") or ""),
        )
        if not stable_id:
            continue

        disk_identity = DiskIdentity(
            path=path,
            stable_id=stable_id,
            expected_size_bytes=_nonnegative_int(root.get("size")),
            model=str(root.get("model") or "").strip(),
            serial=str(root.get("serial") or "").strip(),
        )
        geometry = _probe_parted_geometry(path, run)
        geometry_error = geometry.error or _geometry_mismatch(root, geometry)
        unsupported_descendants = _unsupported_descendant_types(root)
        logical_sector = _positive_int(root.get("log-sec"), default=512)
        partitions = _build_partitions(
            root,
            disk_identity,
            geometry,
            logical_sector,
        )
        free_extents = tuple(
            FreeExtent(stable_id, start, size)
            for start, size in (() if geometry_error else geometry.free_extents)
            if start >= 0 and size > 0
        )
        table = str(root.get("pttype") or "").lower()
        table_uuid = str(root.get("ptuuid") or "").lower()
        topology_digest = _disk_topology_digest(
            disk_identity,
            table,
            table_uuid,
            partitions,
            free_extents,
            geometry_error,
            unsupported_descendants,
        )
        disks.append(
            DiskInventory(
                identity=disk_identity,
                partition_table=table,
                partition_table_uuid=table_uuid,
                partitions=partitions,
                free_extents=free_extents,
                topology_digest=topology_digest,
                geometry_probe_error=geometry_error,
                unsupported_descendant_types=unsupported_descendants,
            )
        )

    ordered = tuple(sorted(disks, key=lambda item: item.identity.stable_id))
    inventory_digest = _digest(
        [
            {
                "stable_id": item.identity.stable_id,
                "size_bytes": item.identity.expected_size_bytes,
                "topology_digest": item.topology_digest,
            }
            for item in ordered
        ]
    )
    return StorageInventory(ordered, inventory_digest)


def bind_disk_topology(
    inventory: StorageInventory, stable_id: str
) -> DiskTopologyBinding:
    disk = inventory.disk(stable_id)
    return DiskTopologyBinding(
        stable_id=disk.identity.stable_id,
        expected_size_bytes=disk.identity.expected_size_bytes,
        topology_digest=disk.topology_digest,
    )


def verify_disk_topology(
    binding: DiskTopologyBinding, inventory: StorageInventory
) -> DiskInventory:
    """Resolve a binding or reject changed/missing topology."""

    try:
        disk = inventory.disk(binding.stable_id)
    except KeyError as error:
        raise StaleStorageInventoryError(
            "Selected disk is no longer present"
        ) from error
    if disk.identity.expected_size_bytes != binding.expected_size_bytes:
        raise StaleStorageInventoryError("Selected disk size changed")
    if disk.topology_digest != binding.topology_digest:
        raise StaleStorageInventoryError("Selected disk topology changed")
    return disk


def _probe_parted_geometry(
    disk: str,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> _PartedGeometry:
    try:
        result = run(
            [
                "parted",
                "--machine",
                "--script",
                disk,
                "unit",
                "B",
                "print",
                "free",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return _PartedGeometry((), (), str(error))
    if result.returncode != 0:
        return _PartedGeometry(
            (), (), result.stderr.strip() or "parted print free failed"
        )
    try:
        return _parse_parted_machine(result.stdout)
    except ValueError as error:
        return _PartedGeometry((), (), f"Invalid parted output: {error}")


def _parse_parted_machine(output: str) -> _PartedGeometry:
    partitions: list[_PartedPartition] = []
    free_extents: list[tuple[int, int]] = []
    saw_header = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "BYT;":
            saw_header = True
            continue
        if not line.endswith(";"):
            continue
        fields = _split_parted_fields(line[:-1])
        if len(fields) < 4:
            continue
        if fields[0].isdigit():
            number = int(fields[0])
            start = _parted_bytes(fields[1])
            size = _parted_bytes(fields[3])
            flags = (
                tuple(
                    sorted(
                        flag.strip().lower()
                        for flag in fields[-1].split(",")
                        if flag.strip()
                    )
                )
                if len(fields) >= 7
                else ()
            )
            partitions.append(_PartedPartition(number, start, size, flags))
            continue
        if any(field.strip().lower() == "free" for field in fields[4:]):
            free_extents.append(
                (_parted_bytes(fields[1]), _parted_bytes(fields[3]))
            )
    if not saw_header:
        raise ValueError("missing BYT header")
    return _PartedGeometry(
        tuple(sorted(partitions, key=lambda item: item.number)),
        tuple(sorted(free_extents)),
    )


def _split_parted_fields(value: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _parted_bytes(value: str) -> int:
    normalized = value.strip()
    if normalized.endswith("B"):
        normalized = normalized[:-1]
    if not normalized.isdigit():
        raise ValueError(f"invalid byte geometry {value!r}")
    return int(normalized)


def _build_partitions(
    root: dict[str, object],
    disk: DiskIdentity,
    geometry: _PartedGeometry,
    logical_sector: int,
) -> tuple[PartitionInventory, ...]:
    by_number = {item.number: item for item in geometry.partitions}
    partitions: list[PartitionInventory] = []
    children = root.get("children") or []
    if not isinstance(children, list):
        return ()
    for child in children:
        if not isinstance(child, dict) or str(child.get("type") or "") != "part":
            continue
        number = _positive_int(child.get("partn"), default=0)
        if number <= 0:
            continue
        parted = by_number.get(number)
        if parted is not None:
            start_bytes = parted.start_bytes
            size_bytes = parted.size_bytes
            flags = parted.flags
        else:
            start_sectors = _nonnegative_int(child.get("start"))
            start_bytes = start_sectors * logical_sector
            size_bytes = _nonnegative_int(child.get("size"))
            flags = ()
        if size_bytes <= 0:
            continue
        mountpoints = child.get("mountpoints") or []
        if not isinstance(mountpoints, list):
            mountpoints = [mountpoints]
        partitions.append(
            PartitionInventory(
                identity=PartitionIdentity(
                    path=str(child.get("path") or ""),
                    number=number,
                    partuuid=str(child.get("partuuid") or "").lower(),
                    start_bytes=start_bytes,
                    size_bytes=size_bytes,
                ),
                parent_disk_id=disk.stable_id,
                partition_type=str(child.get("parttype") or "").lower(),
                filesystem_type=str(child.get("fstype") or "").lower(),
                filesystem_uuid=str(child.get("uuid") or ""),
                filesystem_label=str(child.get("label") or ""),
                mountpoints=tuple(
                    sorted(str(item) for item in mountpoints if item)
                ),
                flags=flags,
            )
        )
    return tuple(
        sorted(
            partitions,
            key=lambda item: (
                item.identity.start_bytes,
                item.identity.number,
            ),
        )
    )


def _geometry_mismatch(
    root: dict[str, object], geometry: _PartedGeometry
) -> str:
    children = root.get("children") or []
    if not isinstance(children, list):
        return "lsblk returned invalid child device data"
    lsblk_numbers: set[int] = set()
    missing_numbers = False
    for child in children:
        if not isinstance(child, dict) or str(child.get("type") or "") != "part":
            continue
        number = _positive_int(child.get("partn"), default=0)
        if number <= 0:
            missing_numbers = True
        else:
            lsblk_numbers.add(number)
    parted_numbers = {item.number for item in geometry.partitions}
    if missing_numbers:
        return "lsblk did not report every partition number"
    if lsblk_numbers != parted_numbers:
        return (
            "lsblk and parted partition sets differ: "
            f"lsblk={sorted(lsblk_numbers)}, parted={sorted(parted_numbers)}"
        )
    return ""


def _disk_topology_digest(
    disk: DiskIdentity,
    table: str,
    table_uuid: str,
    partitions: tuple[PartitionInventory, ...],
    free_extents: tuple[FreeExtent, ...],
    geometry_error: str,
    unsupported_descendant_types: tuple[str, ...] = (),
) -> str:
    return _digest(
        {
            "stable_id": disk.stable_id,
            "size_bytes": disk.expected_size_bytes,
            "partition_table": table,
            "partition_table_uuid": table_uuid,
            "partitions": [
                {
                    "number": item.identity.number,
                    "partuuid": item.identity.partuuid,
                    "start_bytes": item.identity.start_bytes,
                    "size_bytes": item.identity.size_bytes,
                    "partition_type": item.partition_type,
                    "filesystem_type": item.filesystem_type,
                    "filesystem_uuid": item.filesystem_uuid,
                    "mountpoints": list(item.mountpoints),
                    "flags": list(item.flags),
                }
                for item in partitions
            ],
            "free_extents": [
                {
                    "start_bytes": item.start_bytes,
                    "size_bytes": item.size_bytes,
                }
                for item in free_extents
            ],
            "geometry_complete": not bool(geometry_error),
            "unsupported_descendant_types": list(
                unsupported_descendant_types
            ),
        }
    )


def _unsupported_descendant_types(root: dict[str, object]) -> tuple[str, ...]:
    """Return nested mapper/array types unsupported by guided mode."""

    found: list[str] = []

    def walk(node: dict[str, object], *, is_root: bool = False) -> None:
        kind = str(node.get("type") or "").lower()
        if not is_root and kind not in {"part"}:
            found.append(kind or "unknown")
        children = node.get("children") or []
        if not isinstance(children, list):
            found.append("unknown")
            return
        for child in children:
            if isinstance(child, dict):
                walk(child)
            else:
                found.append("unknown")

    walk(root, is_root=True)
    return tuple(sorted(found))


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _nonnegative_int(value: object) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, result)


def _positive_int(value: object, *, default: int) -> int:
    result = _nonnegative_int(value)
    return result if result > 0 else default
