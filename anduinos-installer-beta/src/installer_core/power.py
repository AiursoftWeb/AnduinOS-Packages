"""Read-only system power detection for installer safety routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


DEFAULT_POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")
_SYSTEM_BATTERY_PREFIXES = ("BAT", "CMB")
_EXTERNAL_POWER_TYPES = frozenset(
    {
        "mains",
        "usb",
        "usb_c",
        "usb_pd",
        "usb_dcp",
        "usb_cdp",
        "usb_aca",
        "wireless",
    }
)


class PowerDecision(str, Enum):
    """The three outcomes needed by the welcome-page router."""

    NO_VALID_BATTERY = "no-valid-battery"
    ALLOW_INSTALLATION = "allow-installation"
    REQUIRE_WARNING = "require-warning"


@dataclass(frozen=True)
class PowerProbeResult:
    """One immutable snapshot of the system's installation power state."""

    capacity_percent: int | None
    external_power: bool
    battery_count: int

    @property
    def decision(self) -> PowerDecision:
        if self.capacity_percent is None or self.battery_count < 1:
            return PowerDecision.NO_VALID_BATTERY
        if requires_low_battery_warning(
            self.capacity_percent, self.external_power
        ):
            return PowerDecision.REQUIRE_WARNING
        return PowerDecision.ALLOW_INSTALLATION

    @property
    def requires_warning(self) -> bool:
        return self.decision is PowerDecision.REQUIRE_WARNING


@dataclass(frozen=True)
class _BatteryReading:
    capacity_percent: int
    energy: tuple[int, int] | None
    charge: tuple[int, int] | None
    charging: bool


def requires_low_battery_warning(
    capacity_percent: int | None,
    external_power: bool,
) -> bool:
    """Apply the exact plugged-in and unplugged low-power boundaries."""

    if capacity_percent is None or not 1 <= capacity_percent <= 100:
        return False
    maximum_blocked = 25 if external_power else 45
    return capacity_percent <= maximum_blocked


def probe_power_supply(
    root: str | Path = DEFAULT_POWER_SUPPLY_ROOT,
) -> PowerProbeResult:
    """Probe system batteries and external supplies below a sysfs root.

    Missing directories, disappearing devices, and malformed firmware fields
    are ignored. A battery reporting zero percent is intentionally not treated
    as valid because some battery-less systems expose such a phantom device.
    """

    supply_root = Path(root)
    try:
        devices = tuple(path for path in supply_root.iterdir() if path.is_dir())
    except (OSError, ValueError):
        devices = ()

    batteries: list[_BatteryReading] = []
    external_power = False
    for device in devices:
        supply_type = (_read_text(device / "type") or "").lower()
        if supply_type in _EXTERNAL_POWER_TYPES:
            external_power = external_power or _read_int(device / "online") == 1

        if not _is_system_battery(device, supply_type):
            continue
        reading = _read_battery(device)
        if reading is not None:
            batteries.append(reading)

    if not batteries:
        return PowerProbeResult(None, external_power, 0)

    external_power = external_power or any(
        battery.charging for battery in batteries
    )
    return PowerProbeResult(
        _aggregate_capacity(batteries),
        external_power,
        len(batteries),
    )


def _is_system_battery(device: Path, supply_type: str) -> bool:
    if supply_type != "battery":
        return False
    scope = (_read_text(device / "scope") or "").lower()
    if scope and scope != "system":
        return False
    upper_name = device.name.upper()
    return upper_name.startswith(_SYSTEM_BATTERY_PREFIXES) or scope == "system"


def _read_battery(device: Path) -> _BatteryReading | None:
    raw_capacity = _read_text(device / "capacity")
    energy = _read_pair(device, "energy_now", "energy_full")
    charge = _read_pair(device, "charge_now", "charge_full")

    if raw_capacity is not None:
        capacity = _parse_int(raw_capacity)
        if capacity is None or not 1 <= capacity <= 100:
            return None
    else:
        capacity = _pair_percent(energy) or _pair_percent(charge)
        if capacity is None:
            return None

    status = (_read_text(device / "status") or "").lower()
    return _BatteryReading(capacity, energy, charge, status == "charging")


def _aggregate_capacity(batteries: list[_BatteryReading]) -> int:
    energy_pairs = [battery.energy for battery in batteries]
    if all(pair is not None for pair in energy_pairs):
        return _combined_percent(energy_pairs)

    charge_pairs = [battery.charge for battery in batteries]
    if all(pair is not None for pair in charge_pairs):
        return _combined_percent(charge_pairs)

    return min(battery.capacity_percent for battery in batteries)


def _combined_percent(pairs: list[tuple[int, int] | None]) -> int:
    present = [pair for pair in pairs if pair is not None]
    current = sum(pair[0] for pair in present)
    full = sum(pair[1] for pair in present)
    return max(1, min(100, current * 100 // full))


def _pair_percent(pair: tuple[int, int] | None) -> int | None:
    if pair is None:
        return None
    current, full = pair
    percent = current * 100 // full
    return percent if 1 <= percent <= 100 else None


def _read_pair(
    device: Path, current_name: str, full_name: str
) -> tuple[int, int] | None:
    current = _read_int(device / current_name)
    full = _read_int(device / full_name)
    if current is None or full is None or full <= 0 or not 0 < current <= full:
        return None
    return current, full


def _read_int(path: Path) -> int | None:
    value = _read_text(path)
    return _parse_int(value) if value is not None else None


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
