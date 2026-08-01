"""Read-only eligibility analysis for guided storage coexistence.

This module never plans a resize.  It only identifies already-unallocated
space and whole partitions that could be explicitly discarded later.  The
result carries user-facing safety explanations so a missing free extent can
never degrade into an ambiguous force-continue path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import Firmware
from .storage_inventory import (
    DiskInventory,
    FreeExtent,
    PartitionInventory,
)


MIB = 1024**2
GIB = 1024**3
GUIDED_ROOT_MINIMUM_BYTES = 16 * GIB
GUIDED_SWAP_BYTES = 4096 * MIB
GUIDED_ESP_BYTES = 1024 * MIB
GUIDED_ALIGNMENT_RESERVE_BYTES = 4 * MIB
MINIMUM_REUSABLE_ESP_BYTES = 100 * MIB


class CoexistenceStatus(str, Enum):
    AVAILABLE = "available"
    ACTION_REQUIRED = "action-required"
    UNSUPPORTED = "unsupported"


class CoexistenceBlocker(str, Enum):
    LEGACY_FIRMWARE = "legacy-firmware"
    NON_GPT_DISK = "non-gpt-disk"
    INCOMPLETE_GEOMETRY = "incomplete-geometry"
    UNSTABLE_IDENTITIES = "unstable-identities"
    UNSUPPORTED_MAPPING = "unsupported-mapping"
    DISK_IN_USE = "disk-in-use"
    NO_SUITABLE_FREE_SPACE = "no-suitable-free-space"


class CoexistenceNoticeCode(str, Enum):
    UEFI_GPT_REQUIRED = "uefi-gpt-required"
    GEOMETRY_UNAVAILABLE = "geometry-unavailable"
    IDENTITY_UNAVAILABLE = "identity-unavailable"
    MAPPING_UNSUPPORTED = "mapping-unsupported"
    UNMOUNT_AND_RESCAN = "unmount-and-rescan"
    USES_UNALLOCATED_SPACE_ONLY = "uses-unallocated-space-only"
    PRESERVES_EXISTING_PARTITIONS = "preserves-existing-partitions"
    ESP_REQUIRES_VALIDATION = "esp-requires-validation"
    BITLOCKER_NOT_MODIFIED = "bitlocker-not-modified"
    WINDOWS_STATE_NOT_REPAIRED = "windows-state-not-repaired"
    SHRINK_IN_WINDOWS = "shrink-in-windows"
    DISPOSABLE_PARTITION_OPTION = "disposable-partition-option"
    NO_FORCE_CONTINUE = "no-force-continue"
    RESCAN_AFTER_CHANGES = "rescan-after-changes"


@dataclass(frozen=True)
class CoexistenceNotice:
    code: CoexistenceNoticeCode
    message: str


@dataclass(frozen=True)
class FreeSpaceCandidate:
    extent: FreeExtent
    required_bytes: int
    requires_reused_esp: bool


@dataclass(frozen=True)
class CoexistenceDecision:
    disk_stable_id: str
    status: CoexistenceStatus
    blockers: tuple[CoexistenceBlocker, ...]
    windows_detected: bool
    bitlocker_detected: bool
    esp_candidates: tuple[PartitionInventory, ...]
    free_space_candidates: tuple[FreeSpaceCandidate, ...]
    disposable_partition_candidates: tuple[PartitionInventory, ...]
    notices: tuple[CoexistenceNotice, ...]

    @property
    def can_install_from_free_space(self) -> bool:
        return (
            self.status is CoexistenceStatus.AVAILABLE
            and bool(self.free_space_candidates)
        )


def analyze_guided_coexistence(
    disk: DiskInventory,
    firmware: Firmware,
) -> CoexistenceDecision:
    """Classify safe next actions without modifying or mounting anything."""

    windows_detected = any(
        item.is_windows_partition for item in disk.partitions
    )
    bitlocker_detected = any(
        item.is_bitlocker_partition for item in disk.partitions
    )
    esp_candidates = tuple(
        item
        for item in disk.partitions
        if item.is_efi_filesystem_candidate
        and bool(item.identity.partuuid)
        and bool(item.filesystem_uuid)
        and item.identity.size_bytes >= MINIMUM_REUSABLE_ESP_BYTES
    )
    disposable_candidates = tuple(
        item
        for item in disk.partitions
        if not item.is_windows_critical_partition
        and bool(item.identity.partuuid)
        and not item.mountpoints
        and item.identity.size_bytes >= GUIDED_ROOT_MINIMUM_BYTES
    )

    blockers: list[CoexistenceBlocker] = []
    if firmware is not Firmware.UEFI:
        blockers.append(CoexistenceBlocker.LEGACY_FIRMWARE)
    if disk.partition_table != "gpt":
        blockers.append(CoexistenceBlocker.NON_GPT_DISK)
    if disk.geometry_probe_error:
        blockers.append(CoexistenceBlocker.INCOMPLETE_GEOMETRY)
    partuuids = tuple(item.identity.partuuid for item in disk.partitions)
    if (
        not disk.partition_table_uuid
        or any(not item for item in partuuids)
        or len(set(partuuids)) != len(partuuids)
    ):
        blockers.append(CoexistenceBlocker.UNSTABLE_IDENTITIES)
    if disk.unsupported_descendant_types:
        blockers.append(CoexistenceBlocker.UNSUPPORTED_MAPPING)
    if any(item.mountpoints for item in disk.partitions):
        blockers.append(CoexistenceBlocker.DISK_IN_USE)

    free_candidates = _free_space_candidates(disk, bool(esp_candidates))
    if not free_candidates:
        blockers.append(CoexistenceBlocker.NO_SUITABLE_FREE_SPACE)

    hard_blockers = {
        CoexistenceBlocker.LEGACY_FIRMWARE,
        CoexistenceBlocker.NON_GPT_DISK,
        CoexistenceBlocker.INCOMPLETE_GEOMETRY,
        CoexistenceBlocker.UNSTABLE_IDENTITIES,
        CoexistenceBlocker.UNSUPPORTED_MAPPING,
    }
    if any(item in hard_blockers for item in blockers):
        status = CoexistenceStatus.UNSUPPORTED
    elif blockers:
        status = CoexistenceStatus.ACTION_REQUIRED
    else:
        status = CoexistenceStatus.AVAILABLE

    notices = _notices(
        status=status,
        blockers=tuple(blockers),
        windows_detected=windows_detected,
        bitlocker_detected=bitlocker_detected,
        has_esp_candidate=bool(esp_candidates),
        has_disposable_candidate=bool(disposable_candidates),
        has_free_candidate=bool(free_candidates),
    )
    return CoexistenceDecision(
        disk_stable_id=disk.identity.stable_id,
        status=status,
        blockers=tuple(blockers),
        windows_detected=windows_detected,
        bitlocker_detected=bitlocker_detected,
        esp_candidates=esp_candidates,
        free_space_candidates=free_candidates,
        disposable_partition_candidates=disposable_candidates,
        notices=notices,
    )


def _free_space_candidates(
    disk: DiskInventory,
    has_esp_candidate: bool,
) -> tuple[FreeSpaceCandidate, ...]:
    required_with_reused_esp = (
        GUIDED_ROOT_MINIMUM_BYTES
        + GUIDED_SWAP_BYTES
        + GUIDED_ALIGNMENT_RESERVE_BYTES
    )
    required_with_new_esp = required_with_reused_esp + GUIDED_ESP_BYTES
    candidates: list[FreeSpaceCandidate] = []
    for extent in disk.free_extents:
        if extent.size_bytes >= required_with_new_esp:
            candidates.append(
                FreeSpaceCandidate(
                    extent=extent,
                    required_bytes=required_with_new_esp,
                    requires_reused_esp=False,
                )
            )
        elif has_esp_candidate and extent.size_bytes >= required_with_reused_esp:
            candidates.append(
                FreeSpaceCandidate(
                    extent=extent,
                    required_bytes=required_with_reused_esp,
                    requires_reused_esp=True,
                )
            )
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.extent.start_bytes,
                item.extent.size_bytes,
            ),
        )
    )


def _notices(
    *,
    status: CoexistenceStatus,
    blockers: tuple[CoexistenceBlocker, ...],
    windows_detected: bool,
    bitlocker_detected: bool,
    has_esp_candidate: bool,
    has_disposable_candidate: bool,
    has_free_candidate: bool,
) -> tuple[CoexistenceNotice, ...]:
    notices: list[CoexistenceNotice] = []
    if CoexistenceBlocker.LEGACY_FIRMWARE in blockers or (
        CoexistenceBlocker.NON_GPT_DISK in blockers
    ):
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.UEFI_GPT_REQUIRED,
                "Guided coexistence requires a system booted in UEFI mode "
                "and a GPT target disk. This disk cannot continue in guided "
                "mode.",
            )
        )
    if CoexistenceBlocker.INCOMPLETE_GEOMETRY in blockers:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.GEOMETRY_UNAVAILABLE,
                "The complete partition map and free-space geometry could "
                "not be read consistently. No space on this disk is "
                "authorized for installation.",
            )
        )
    if CoexistenceBlocker.UNSTABLE_IDENTITIES in blockers:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.IDENTITY_UNAVAILABLE,
                "The GPT disk or one of its partitions has no unique stable "
                "identifier. Guided coexistence cannot safely authorize "
                "preservation or writes on this disk.",
            )
        )
    if CoexistenceBlocker.UNSUPPORTED_MAPPING in blockers:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.MAPPING_UNSUPPORTED,
                "This disk contains an active mapper, array or other nested "
                "block-device topology that guided coexistence does not "
                "support.",
            )
        )
    if CoexistenceBlocker.DISK_IN_USE in blockers:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.UNMOUNT_AND_RESCAN,
                "A partition on this disk is mounted. Unmount it and rescan "
                "storage before continuing.",
            )
        )

    if has_free_candidate and status is not CoexistenceStatus.UNSUPPORTED:
        notices.extend(
            (
                CoexistenceNotice(
                    CoexistenceNoticeCode.USES_UNALLOCATED_SPACE_ONLY,
                    "AnduinOS will use only the selected unallocated space. "
                    "The installer will not shrink or move an existing "
                    "filesystem.",
                ),
                CoexistenceNotice(
                    CoexistenceNoticeCode.PRESERVES_EXISTING_PARTITIONS,
                    "Existing Windows, recovery and data partitions remain "
                    "outside the write set and will be preserved.",
                ),
            )
        )
    if has_esp_candidate and status is not CoexistenceStatus.UNSUPPORTED:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.ESP_REQUIRES_VALIDATION,
                "The existing EFI System Partition is only a candidate. "
                "Health and free-space checks must pass before it can be "
                "reused, and it will never be formatted.",
            )
        )
    if bitlocker_detected:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.BITLOCKER_NOT_MODIFIED,
                "BitLocker storage will be preserved. The installer will "
                "not unlock, resize, repair or otherwise modify it.",
            )
        )
    if windows_detected:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.WINDOWS_STATE_NOT_REPAIRED,
                "The installer will not mount, repair or infer the safety of "
                "Windows volumes from hibernation or Fast Startup state. "
                "Any required Windows maintenance must be completed in "
                "Windows.",
            )
        )
    if not has_free_candidate and status is not CoexistenceStatus.UNSUPPORTED:
        owner = (
            "Windows Disk Management"
            if windows_detected
            else "a partitioning tool"
        )
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.SHRINK_IN_WINDOWS,
                "No suitable unallocated space was found. To protect your "
                f"data, create unallocated space with {owner}, then boot "
                "the installer again and rescan the disk.",
            )
        )
    if has_disposable_candidate and status is not CoexistenceStatus.UNSUPPORTED:
        notices.append(
            CoexistenceNotice(
                CoexistenceNoticeCode.DISPOSABLE_PARTITION_OPTION,
                "Alternatively, you may explicitly select one entire "
                "partition to erase. Everything in that selected partition "
                "will be destroyed; it is never selected automatically.",
            )
        )
    if status is not CoexistenceStatus.AVAILABLE:
        notices.extend(
            (
                CoexistenceNotice(
                    CoexistenceNoticeCode.NO_FORCE_CONTINUE,
                    "Installation cannot continue with this selection. "
                    "There is no force-continue option around storage safety "
                    "checks.",
                ),
                CoexistenceNotice(
                    CoexistenceNoticeCode.RESCAN_AFTER_CHANGES,
                    "After changing partitions or unmounting a volume, "
                    "rescan storage and select the target again.",
                ),
            )
        )
    return tuple(notices)
