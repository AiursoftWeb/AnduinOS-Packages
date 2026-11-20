"""AnduinOS version detection.

AnduinOS is based on Ubuntu/Debian. We try multiple sources to determine
the running AnduinOS version, falling back gracefully on non-AnduinOS
systems (useful for development/testing).
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from anduinos_help.utils.logging import get_logger

_log = get_logger("system.version")

_OS_RELEASE_PATHS = ("/etc/os-release", "/usr/lib/os-release")
_LSB_RELEASE_PATH = "/etc/lsb-release"
_ANDUINOS_FILE_PATHS = ("/etc/anduin-os-version", "/etc/anduin_version")


@dataclass
class SystemVersion:
    is_anduinos: bool
    anduinos_version: str | None
    anduinos_codename: str | None
    base_distro: str | None
    base_version: str | None
    kernel: str | None
    desktop: str | None
    session_type: str | None  # x11 | wayland

    def short(self) -> str:
        if self.is_anduinos:
            return f"AnduinOS {self.anduinos_version or ''}".strip()
        if self.base_distro:
            return f"{self.base_distro} {self.base_version or ''}".strip()
        return "Unknown"

    def major_version(self) -> str | None:
        v = self.anduinos_version or self.base_version
        if not v:
            return None
        m = re.match(r"(\d+)", v)
        return f"{m.group(1)}.x" if m else None


def detect() -> SystemVersion:
    """Detect the running system version."""
    info: dict[str, str] = {}
    for p in _OS_RELEASE_PATHS:
        if os.path.exists(p):
            info.update(_parse_os_release(p))
            break

    is_anduinos = info.get("ID", "").lower() == "anduin" or info.get("NAME", "").lower().startswith("anduin")
    anduinos_version = info.get("VERSION_ID") if is_anduinos else None
    anduinos_codename = info.get("VERSION_CODENAME") if is_anduinos else None

    # /etc/anduin-os-version overrides anything else.
    for p in _ANDUINOS_FILE_PATHS:
        try:
            content = Path(p).read_text(encoding="utf-8").strip()
            if content:
                # Format may be a single line "2.0.3 (codename)"
                m = re.match(r"^\s*([\d.]+)(?:\s+\(([^)]+)\))?", content)
                if m:
                    is_anduinos = True
                    anduinos_version = m.group(1)
                    if m.group(2):
                        anduinos_codename = m.group(2)
                break
        except OSError:
            continue

    base_distro = info.get("ID") if not is_anduinos else info.get("ID_LIKE", "").split()[0] if info.get("ID_LIKE") else "ubuntu"
    base_version = info.get("VERSION_ID") if not is_anduinos else None

    kernel = _read_kernel()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP")
    session_type = os.environ.get("XDG_SESSION_TYPE")

    return SystemVersion(
        is_anduinos=is_anduinos,
        anduinos_version=anduinos_version,
        anduinos_codename=anduinos_codename,
        base_distro=base_distro,
        base_version=base_version,
        kernel=kernel,
        desktop=desktop,
        session_type=session_type,
    )


def applicable_doc_version() -> str:
    """Return the documentation version label that applies to this system."""
    sv = detect()
    if sv.is_anduinos and sv.anduinos_version:
        m = re.match(r"(\d+)", sv.anduinos_version)
        return f"{m.group(1)}.x" if m else "2.x"
    if sv.base_distro and sv.base_distro.lower() in {"ubuntu", "debian"}:
        return "2.x"
    return "2.x"


def _parse_os_release(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _read_kernel() -> str | None:
    try:
        with open("/proc/sys/kernel/osrelease", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None
