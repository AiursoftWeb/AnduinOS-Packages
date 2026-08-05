"""Friendly, collision-resistant hostname suggestions for new installs."""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from pathlib import Path


DEFAULT_HOSTNAME_PREFIX = "anduinos"
HOSTNAME_SUFFIX_LENGTH = 4
_LAPTOP_CHASSIS_TYPES = frozenset({8, 9, 10, 11, 14, 30, 31, 32})
_DESKTOP_CHASSIS_TYPES = frozenset(
    {
        3, 4, 5, 6, 7, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
        25, 26, 27, 28, 29, 33, 34, 35, 36,
    }
)
_HOSTNAME_COMPONENT_RE = re.compile(r"[^a-z0-9]+")
_RANDOM_SUFFIX_RE = re.compile(r"^[0-9a-f]{4}$")


def generate_random_suffix(
    randbelow: Callable[[int], int] = secrets.randbelow,
) -> str:
    """Return four lowercase hexadecimal digits from a secure RNG."""
    return f"{randbelow(16**HOSTNAME_SUFFIX_LENGTH):04x}"


def detect_device_type(
    chassis_type_path: Path = Path("/sys/class/dmi/id/chassis_type"),
    power_supply_path: Path = Path("/sys/class/power_supply"),
) -> str:
    """Classify the live system as ``laptop`` or ``desktop``.

    SMBIOS chassis type is authoritative when available.  A system battery is
    used as a fallback for devices, including some ARM laptops, without DMI.
    Unknown devices deliberately fall back to ``desktop``.
    """
    chassis_type = _read_chassis_type(chassis_type_path)
    if chassis_type in _LAPTOP_CHASSIS_TYPES:
        return "laptop"
    if chassis_type in _DESKTOP_CHASSIS_TYPES:
        return "desktop"
    if _has_system_battery(power_supply_path):
        return "laptop"
    return "desktop"


def suggest_hostname(
    username: str,
    device_type: str,
    random_suffix: str,
) -> str:
    """Build ``username-device-suffix`` within the DNS label limit."""
    if not _RANDOM_SUFFIX_RE.fullmatch(random_suffix):
        raise ValueError(
            "random_suffix must be four lowercase hexadecimal digits"
        )

    normalized_username = (
        _normalize_component(username) or DEFAULT_HOSTNAME_PREFIX
    )
    normalized_device = "laptop" if device_type == "laptop" else "desktop"
    fixed_suffix = f"-{normalized_device}-{random_suffix}"
    username_limit = 63 - len(fixed_suffix)
    normalized_username = normalized_username[:username_limit].rstrip("-")
    return f"{normalized_username or DEFAULT_HOSTNAME_PREFIX}{fixed_suffix}"


def _normalize_component(value: str) -> str:
    return _HOSTNAME_COMPONENT_RE.sub("-", value.strip().lower()).strip("-")


def _read_chassis_type(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _has_system_battery(path: Path) -> bool:
    try:
        supplies = tuple(path.iterdir())
    except OSError:
        return False

    for supply in supplies:
        name = supply.name.upper()
        if name.startswith(("BAT", "CMB")):
            return True
        try:
            supply_type = (
                (supply / "type").read_text(encoding="utf-8").strip()
            )
            scope_path = supply / "scope"
            scope = (
                scope_path.read_text(encoding="utf-8").strip()
                if scope_path.exists()
                else ""
            )
        except OSError:
            continue
        if supply_type == "Battery" and scope == "System":
            return True
    return False
