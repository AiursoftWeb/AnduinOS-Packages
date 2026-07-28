"""Deterministic partition layouts for erase-disk installations."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Architecture, Filesystem, Firmware, InstallMode, InstallPlan


MIB = 1024 * 1024
BIOS_GRUB_SIZE_MIB = 2
ALIGNMENT_MIB = 1


@dataclass(frozen=True)
class PartitionSpec:
    number: int
    name: str
    start_mib: int
    end_mib: int | None
    filesystem: str | None
    mount_point: str | None
    flags: tuple[str, ...] = ()

    @property
    def size_mib(self) -> int | None:
        if self.end_mib is None:
            return None
        return self.end_mib - self.start_mib


@dataclass(frozen=True)
class PartitionLayout:
    table: str
    partitions: tuple[PartitionSpec, ...]

    def partition(self, name: str) -> PartitionSpec:
        for item in self.partitions:
            if item.name == name:
                return item
        raise KeyError(name)


def build_erase_disk_layout(plan: InstallPlan) -> PartitionLayout:
    """Build the release layout without touching a block device.

    amd64 always receives both BIOS boot and EFI System partitions.  This
    keeps an erased disk bootable when old firmware changes between CSM and
    UEFI.  arm64 supports standards-based UEFI only.
    """
    if plan.storage.mode is not InstallMode.ERASE_DISK:
        raise ValueError("Only erase-disk layouts are implemented")

    cursor = ALIGNMENT_MIB
    parts: list[PartitionSpec] = []

    if plan.platform.architecture is Architecture.AMD64:
        parts.append(
            PartitionSpec(
                number=1,
                name="bios-boot",
                start_mib=cursor,
                end_mib=cursor + BIOS_GRUB_SIZE_MIB,
                filesystem=None,
                mount_point=None,
                flags=("bios_grub",),
            )
        )
        cursor += BIOS_GRUB_SIZE_MIB

    esp_number = len(parts) + 1
    parts.append(
        PartitionSpec(
            number=esp_number,
            name="efi-system",
            start_mib=cursor,
            end_mib=cursor + plan.storage.esp_size_mib,
            filesystem="fat32",
            mount_point="/boot/efi",
            flags=("esp",),
        )
    )
    cursor += plan.storage.esp_size_mib

    swap_number = len(parts) + 1
    parts.append(
        PartitionSpec(
            number=swap_number,
            name="swap",
            start_mib=cursor,
            end_mib=cursor + plan.storage.swap_size_mib,
            filesystem="linux-swap",
            mount_point=None,
            flags=("swap",),
        )
    )
    cursor += plan.storage.swap_size_mib

    root_number = len(parts) + 1
    parts.append(
        PartitionSpec(
            number=root_number,
            name="root",
            start_mib=cursor,
            end_mib=None,
            filesystem=plan.storage.filesystem.value,
            mount_point="/",
        )
    )

    return PartitionLayout(table="gpt", partitions=tuple(parts))
