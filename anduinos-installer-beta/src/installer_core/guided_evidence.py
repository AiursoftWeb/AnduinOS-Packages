"""Before/after evidence for the destructive guided-coexistence VM campaign."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .esp import EspTreeEntry
from .model import InstallPlan
from .storage_inventory import StorageInventory
from .storage_preservation import (
    GuidedPreservationSnapshot,
    PreservedPartition,
    verify_guided_storage_result,
)


EVIDENCE_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BOOT_ENTRY_RE = re.compile(r"^Boot(?P<number>[0-9A-Fa-f]{4})\*?\s+(?P<body>.+)$")


@dataclass(frozen=True)
class PreservedPartitionDigest:
    partuuid: str
    sha256: str


@dataclass(frozen=True)
class NvramEvidence:
    boot_order: tuple[str, ...]
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class GuidedVmEvidence:
    schema_version: int
    plan_sha256: str
    preservation: GuidedPreservationSnapshot
    partition_digests: tuple[PreservedPartitionDigest, ...]
    reused_esp_partuuid: str
    esp_entries: tuple[EspTreeEntry, ...]
    nvram: NvramEvidence

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "GuidedVmEvidence":
        if not isinstance(value, dict):
            raise ValueError("Evidence must be an object")
        expected = {
            "schema_version",
            "plan_sha256",
            "preservation",
            "partition_digests",
            "reused_esp_partuuid",
            "esp_entries",
            "nvram",
        }
        if set(value) != expected:
            raise ValueError("Evidence contains missing or unknown fields")
        preservation = _preservation_from_dict(value["preservation"])
        digests = tuple(
            _partition_digest_from_dict(item)
            for item in _object_list(value["partition_digests"])
        )
        esp_entries = tuple(
            _esp_entry_from_dict(item)
            for item in _object_list(value["esp_entries"])
        )
        nvram_value = value["nvram"]
        if not isinstance(nvram_value, dict) or set(nvram_value) != {
            "boot_order",
            "entries",
        }:
            raise ValueError("Invalid NVRAM evidence")
        boot_order = nvram_value["boot_order"]
        entries = nvram_value["entries"]
        if not isinstance(boot_order, list) or not isinstance(entries, list):
            raise ValueError("Invalid NVRAM evidence arrays")
        if not all(
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(field, str) for field in item)
            for item in entries
        ):
            raise ValueError("Invalid NVRAM evidence entries")
        evidence = cls(
            schema_version=int(value["schema_version"]),
            plan_sha256=str(value["plan_sha256"]),
            preservation=preservation,
            partition_digests=digests,
            reused_esp_partuuid=str(value["reused_esp_partuuid"]),
            esp_entries=esp_entries,
            nvram=NvramEvidence(
                boot_order=tuple(str(item) for item in boot_order),
                entries=tuple((item[0], item[1]) for item in entries),
            ),
        )
        validate_evidence(evidence)
        return evidence


def plan_sha256(plan: InstallPlan) -> str:
    encoded = json.dumps(
        plan.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def hash_partition(path: Path, expected_size_bytes: int) -> str:
    """Hash exactly one preserve-marked partition without following paths."""

    digest = hashlib.sha256()
    remaining = expected_size_bytes
    with path.open("rb", buffering=0) as stream:
        while remaining:
            chunk = stream.read(min(4 * 1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError(f"Partition ended early: {path}")
            remaining -= len(chunk)
            digest.update(chunk)
        if stream.read(1):
            raise RuntimeError(f"Partition is larger than authorized: {path}")
    return digest.hexdigest()


def capture_partition_digests(
    snapshot: GuidedPreservationSnapshot,
    inventory: StorageInventory,
    *,
    reused_esp_partuuid: str,
) -> tuple[PreservedPartitionDigest, ...]:
    disk = inventory.disk(snapshot.disk_stable_id)
    current = {item.identity.partuuid: item for item in disk.partitions}
    result = []
    for preserved in snapshot.partitions:
        if preserved.partuuid == reused_esp_partuuid:
            continue
        partition = current[preserved.partuuid]
        result.append(
            PreservedPartitionDigest(
                partuuid=preserved.partuuid,
                sha256=hash_partition(
                    Path(partition.identity.path),
                    preserved.size_bytes,
                ),
            )
        )
    return tuple(result)


def verify_partition_digests(
    evidence: GuidedVmEvidence,
    inventory: StorageInventory,
) -> None:
    disk = inventory.disk(evidence.preservation.disk_stable_id)
    current = {item.identity.partuuid: item for item in disk.partitions}
    for expected in evidence.partition_digests:
        preserved = next(
            item
            for item in evidence.preservation.partitions
            if item.partuuid == expected.partuuid
        )
        partition = current.get(expected.partuuid)
        if partition is None:
            raise RuntimeError(
                f"Preserved partition disappeared: {expected.partuuid}"
            )
        actual = hash_partition(
            Path(partition.identity.path), preserved.size_bytes
        )
        if actual != expected.sha256:
            raise RuntimeError(
                f"Preserved partition content changed: {expected.partuuid}"
            )


def capture_nvram_evidence(output: str) -> NvramEvidence:
    order: tuple[str, ...] = ()
    entries = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("BootOrder:"):
            order = tuple(
                item.strip().upper()
                for item in line.removeprefix("BootOrder:").split(",")
                if item.strip()
            )
            continue
        match = BOOT_ENTRY_RE.match(line)
        if match is None or _is_anduinos_entry(match.group("body")):
            continue
        entries.append(
            (
                match.group("number").upper(),
                _normalize_nvram_body(match.group("body")),
            )
        )
    entry_numbers = {number for number, _body in entries}
    return NvramEvidence(
        boot_order=tuple(item for item in order if item in entry_numbers),
        entries=tuple(sorted(entries)),
    )


def verify_nvram_evidence(expected: NvramEvidence, output: str) -> None:
    actual = capture_nvram_evidence(output)
    actual_entries = dict(actual.entries)
    for number, body in expected.entries:
        if actual_entries.get(number) != body:
            raise RuntimeError(f"Existing NVRAM entry changed: Boot{number}")
    positions = [
        actual.boot_order.index(item)
        for item in expected.boot_order
        if item in actual.boot_order
    ]
    if len(positions) != len(expected.boot_order) or positions != sorted(positions):
        raise RuntimeError("Existing UEFI boot order changed")


def verify_guided_evidence_topology(
    plan: InstallPlan,
    evidence: GuidedVmEvidence,
    inventory: StorageInventory,
) -> None:
    if plan_sha256(plan) != evidence.plan_sha256:
        raise RuntimeError("Evidence was captured for a different plan")
    verify_guided_storage_result(plan, evidence.preservation, inventory)
    verify_partition_digests(evidence, inventory)


def validate_evidence(evidence: GuidedVmEvidence) -> None:
    if evidence.schema_version != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("Unsupported evidence schema version")
    if not SHA256_RE.fullmatch(evidence.plan_sha256):
        raise ValueError("Invalid evidence plan digest")
    partuuids = {item.partuuid for item in evidence.preservation.partitions}
    digest_ids = [item.partuuid for item in evidence.partition_digests]
    expected_digest_ids = partuuids - (
        {evidence.reused_esp_partuuid}
        if evidence.reused_esp_partuuid
        else set()
    )
    if (
        len(digest_ids) != len(set(digest_ids))
        or set(digest_ids) != expected_digest_ids
    ):
        raise ValueError("Invalid preserved partition digests")
    if any(
        not item.partuuid or not SHA256_RE.fullmatch(item.sha256)
        for item in evidence.partition_digests
    ):
        raise ValueError("Invalid preserved partition digest value")
    if evidence.reused_esp_partuuid:
        if evidence.reused_esp_partuuid not in partuuids:
            raise ValueError("Evidence ESP is not preserve-marked")
        if evidence.reused_esp_partuuid in digest_ids:
            raise ValueError("Shared ESP must use file evidence, not a raw hash")
    elif evidence.esp_entries:
        raise ValueError("Dedicated ESP plans cannot carry shared ESP evidence")
    esp_paths = [item.relative_path.casefold() for item in evidence.esp_entries]
    if len(esp_paths) != len(set(esp_paths)) or any(
        not _valid_esp_entry(item) for item in evidence.esp_entries
    ):
        raise ValueError("Invalid shared ESP evidence")
    if not evidence.nvram.entries or not evidence.nvram.boot_order:
        raise ValueError("Evidence requires an existing UEFI boot entry")
    entry_numbers = [item[0] for item in evidence.nvram.entries]
    if (
        len(entry_numbers) != len(set(entry_numbers))
        or any(not re.fullmatch(r"[0-9A-F]{4}", item) for item in entry_numbers)
        or len(evidence.nvram.boot_order)
        != len(set(evidence.nvram.boot_order))
        or any(item not in entry_numbers for item in evidence.nvram.boot_order)
    ):
        raise ValueError("Invalid existing NVRAM evidence")
    if not any(
        body.startswith("windows boot manager ")
        and r"file(\efi\microsoft\boot\bootmgfw.efi)" in body
        for _number, body in evidence.nvram.entries
    ):
        raise ValueError("Evidence requires the paired Windows boot entry")


def _preservation_from_dict(value: object) -> GuidedPreservationSnapshot:
    if not isinstance(value, dict):
        raise ValueError("Invalid preservation evidence")
    fields = {
        "disk_stable_id",
        "disk_size_bytes",
        "partition_table",
        "partition_table_uuid",
        "partitions",
    }
    if set(value) != fields:
        raise ValueError("Invalid preservation evidence fields")
    return GuidedPreservationSnapshot(
        disk_stable_id=str(value["disk_stable_id"]),
        disk_size_bytes=int(value["disk_size_bytes"]),
        partition_table=str(value["partition_table"]),
        partition_table_uuid=str(value["partition_table_uuid"]),
        partitions=tuple(
            _preserved_partition_from_dict(item)
            for item in _object_list(value["partitions"])
        ),
    )


def _object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise ValueError("Evidence array contains invalid entries")
    return value


def _partition_digest_from_dict(
    value: dict[str, object],
) -> PreservedPartitionDigest:
    if set(value) != {"partuuid", "sha256"}:
        raise ValueError("Invalid preserved partition digest fields")
    if not all(isinstance(value[field], str) for field in value):
        raise ValueError("Invalid preserved partition digest values")
    return PreservedPartitionDigest(value["partuuid"], value["sha256"])


def _esp_entry_from_dict(value: dict[str, object]) -> EspTreeEntry:
    if set(value) != {"relative_path", "kind", "size_bytes", "sha256"}:
        raise ValueError("Invalid shared ESP evidence fields")
    if (
        not isinstance(value["relative_path"], str)
        or not isinstance(value["kind"], str)
        or not isinstance(value["size_bytes"], int)
        or isinstance(value["size_bytes"], bool)
        or not isinstance(value["sha256"], str)
    ):
        raise ValueError("Invalid shared ESP evidence values")
    return EspTreeEntry(
        relative_path=value["relative_path"],
        kind=value["kind"],
        size_bytes=value["size_bytes"],
        sha256=value["sha256"],
    )


def _preserved_partition_from_dict(
    value: dict[str, object],
) -> PreservedPartition:
    expected = {
        "number",
        "partuuid",
        "start_bytes",
        "size_bytes",
        "partition_type",
        "filesystem_type",
        "filesystem_uuid",
        "flags",
    }
    if set(value) != expected or not isinstance(value["flags"], list):
        raise ValueError("Invalid preserved partition evidence")
    return PreservedPartition(
        number=int(value["number"]),
        partuuid=str(value["partuuid"]),
        start_bytes=int(value["start_bytes"]),
        size_bytes=int(value["size_bytes"]),
        partition_type=str(value["partition_type"]),
        filesystem_type=str(value["filesystem_type"]),
        filesystem_uuid=str(value["filesystem_uuid"]),
        flags=tuple(str(item) for item in value["flags"]),
    )


def _is_anduinos_entry(body: str) -> bool:
    return body.casefold().startswith("anduinos ")


def _normalize_nvram_body(body: str) -> str:
    return " ".join(body.split()).casefold()


def _valid_esp_entry(entry: EspTreeEntry) -> bool:
    path = Path(entry.relative_path)
    if (
        not entry.relative_path
        or path.is_absolute()
        or entry.relative_path != path.as_posix()
        or "\\" in entry.relative_path
        or "." in path.parts
        or ".." in path.parts
        or entry.size_bytes < 0
    ):
        return False
    if entry.kind == "directory":
        return entry.size_bytes == 0 and entry.sha256 == ""
    if entry.kind == "file":
        return bool(SHA256_RE.fullmatch(entry.sha256))
    return False
