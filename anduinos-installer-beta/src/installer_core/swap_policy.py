"""Pure disk-swap sizing policy and physical-memory probe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MIB = 1024**2
GIB = 1024**3
MINIMUM_DISK_SWAP_MIB = 2 * 1024
MAXIMUM_RUNTIME_SWAP_MIB = 64 * 1024
MINIMUM_ROOT_MIB = 20 * 1024
DEFAULT_ESP_MIB = 1024
# Whole-disk and guided layouts reserve a small amount for GPT metadata,
# initial alignment and (on amd64) the BIOS boot partition.
LAYOUT_OVERHEAD_MIB = 4


class SwapSizingError(ValueError):
    """The installation space cannot satisfy the minimum swap policy."""


@dataclass(frozen=True)
class SwapSizing:
    physical_memory_bytes: int
    installation_space_bytes: int
    disk_budget_mib: int
    runtime_target_mib: int
    hibernation_target_mib: int
    swap_size_mib: int
    maximum_custom_mib: int
    hibernation_capacity: bool


def calculate_swap_sizing(
    physical_memory_bytes: int,
    installation_space_bytes: int,
    *,
    esp_size_mib: int = DEFAULT_ESP_MIB,
    layout_overhead_mib: int = LAYOUT_OVERHEAD_MIB,
) -> SwapSizing:
    """Return the deterministic disk-swap size for one installation area.

    Priority order:

    1. Reserve at least 2 GiB of disk swap.
    2. Never consume the final 20 GiB intended for the root filesystem.
    3. Prefer RAM rounded up to GiB plus 1 GiB, so the layout has capacity
       for hibernation.  If that cannot fit, use the normal RAM/2 target,
       capped at 64 GiB, instead of wasting space on a still-insufficient
       hibernation partition.

    ``installation_space_bytes`` is the whole target disk for erase-disk
    installs and the selected free extent for guided coexistence.
    """

    _require_positive_integer(physical_memory_bytes, "Physical memory")
    _require_positive_integer(installation_space_bytes, "Installation space")
    _require_nonnegative_integer(esp_size_mib, "ESP size")
    _require_nonnegative_integer(layout_overhead_mib, "Layout overhead")

    rounded_ram_mib = _ceil_to_gib_mib(physical_memory_bytes)
    runtime_target_mib = min(
        MAXIMUM_RUNTIME_SWAP_MIB,
        max(
            MINIMUM_DISK_SWAP_MIB,
            _ceil_to_gib_mib(_ceil_div(physical_memory_bytes, 2)),
        ),
    )
    hibernation_target_mib = max(
        MINIMUM_DISK_SWAP_MIB,
        rounded_ram_mib + 1024,
    )

    reserved_bytes = (
        MINIMUM_ROOT_MIB + esp_size_mib + layout_overhead_mib
    ) * MIB
    available_bytes = installation_space_bytes - reserved_bytes
    disk_budget_mib = max(0, available_bytes // GIB * 1024)
    if disk_budget_mib < MINIMUM_DISK_SWAP_MIB:
        minimum_bytes = reserved_bytes + MINIMUM_DISK_SWAP_MIB * MIB
        raise SwapSizingError(
            "Installation space cannot provide 2 GiB swap while leaving "
            f"20 GiB for root; at least {minimum_bytes} bytes are required"
        )

    if disk_budget_mib >= hibernation_target_mib:
        swap_size_mib = hibernation_target_mib
        hibernation_capacity = True
    else:
        swap_size_mib = min(runtime_target_mib, disk_budget_mib)
        hibernation_capacity = False
    memory_ceiling_mib = max(
        MINIMUM_DISK_SWAP_MIB,
        (physical_memory_bytes * 4 // GIB) * 1024,
    )
    maximum_custom_mib = min(disk_budget_mib, memory_ceiling_mib)

    return SwapSizing(
        physical_memory_bytes=physical_memory_bytes,
        installation_space_bytes=installation_space_bytes,
        disk_budget_mib=disk_budget_mib,
        runtime_target_mib=runtime_target_mib,
        hibernation_target_mib=hibernation_target_mib,
        swap_size_mib=swap_size_mib,
        maximum_custom_mib=maximum_custom_mib,
        hibernation_capacity=hibernation_capacity,
    )


def validate_disk_swap_selection(
    swap_size_mib: int,
    sizing: SwapSizing,
) -> None:
    """Validate one user-selected disk-swap capacity.

    Zero deliberately means zram-only.  Disk swap otherwise uses whole-GiB
    sizing so the UI, partition graph and privileged executor share one exact
    representation.
    """

    if (
        type(swap_size_mib) is not int
        or swap_size_mib < 0
        or swap_size_mib % 1024
    ):
        raise SwapSizingError(
            "Disk swap must be zero or a non-negative whole-GiB value"
        )
    if swap_size_mib > sizing.maximum_custom_mib:
        raise SwapSizingError(
            "Disk swap exceeds the safe maximum of "
            f"{sizing.maximum_custom_mib // 1024} GiB for the current "
            "memory and installation space"
        )


def disk_swap_choices_mib(sizing: SwapSizing) -> tuple[int, ...]:
    """Return discrete non-linear slider values with precise low capacities."""

    maximum_gib = sizing.maximum_custom_mib // 1024
    values = {0, maximum_gib}
    value = 1
    while value <= maximum_gib:
        values.add(value)
        if value < 16:
            value += 1
        elif value < 32:
            value += 2
        elif value < 64:
            value += 4
        elif value < 128:
            value += 8
        else:
            value += 16
    values.update(
        target // 1024
        for target in (
            sizing.runtime_target_mib,
            sizing.hibernation_target_mib,
            sizing.swap_size_mib,
        )
        if target <= sizing.maximum_custom_mib
    )
    return tuple(value * 1024 for value in sorted(values))


def probe_physical_memory_bytes(
    meminfo_path: Path = Path("/proc/meminfo"),
) -> int:
    """Read Linux MemTotal without invoking locale-sensitive commands."""

    try:
        lines = meminfo_path.read_text(encoding="ascii").splitlines()
    except OSError as error:
        raise RuntimeError(f"Could not read physical memory: {error}") from error
    for line in lines:
        name, separator, value = line.partition(":")
        if name != "MemTotal" or not separator:
            continue
        fields = value.split()
        if len(fields) != 2 or fields[1] != "kB" or not fields[0].isdigit():
            break
        memory_bytes = int(fields[0]) * 1024
        if memory_bytes > 0:
            return memory_bytes
        break
    raise RuntimeError("Could not parse MemTotal from /proc/meminfo")


def _ceil_to_gib_mib(size_bytes: int) -> int:
    return _ceil_div(size_bytes, GIB) * 1024


def _ceil_div(dividend: int, divisor: int) -> int:
    return -(-dividend // divisor)


def _require_positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer number of bytes")


def _require_nonnegative_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer MiB value")
