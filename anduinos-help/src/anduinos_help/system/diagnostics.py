"""Safe system diagnostics.

Collects non-sensitive system information useful for bug reports and
the "Diagnostic Center" UI. Sensitive paths (``/etc/shadow``,
``~/.ssh``, ``~/.config``, etc.) are never read.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from anduinos_help.system import version as sys_version
from anduinos_help.utils.logging import get_logger

_log = get_logger("system.diagnostics")

# Files that are NEVER read.
_SENSITIVE_FILES = (
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
)
# Files safe to read for hardware info.
_SAFE_FILES = {
    "cpuinfo": "/proc/cpuinfo",
    "meminfo": "/proc/meminfo",
    "uptime": "/proc/uptime",
}


@dataclass
class DiagnosticsReport:
    system: dict[str, str] = field(default_factory=dict)
    hardware: dict[str, str] = field(default_factory=dict)
    network: dict[str, str] = field(default_factory=dict)
    storage: dict[str, str] = field(default_factory=dict)
    desktop: dict[str, str] = field(default_factory=dict)
    package_managers: dict[str, bool] = field(default_factory=dict)

    def to_text(self) -> str:
        lines: list[str] = []
        for title, section in (
            ("System", self.system),
            ("Desktop", self.desktop),
            ("Hardware", self.hardware),
            ("Network", self.network),
            ("Storage", self.storage),
            ("Package Managers", self.package_managers),
        ):
            lines.append(f"=== {title} ===")
            for k, v in section.items():
                lines.append(f"  {k}: {v}")
            lines.append("")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        lines: list[str] = []
        for title, section in (
            ("System", self.system),
            ("Desktop", self.desktop),
            ("Hardware", self.hardware),
            ("Network", self.network),
            ("Storage", self.storage),
            ("Package Managers", self.package_managers),
        ):
            lines.append(f"## {title}")
            lines.append("")
            if isinstance(section, dict):
                for k, v in section.items():
                    lines.append(f"- **{k}**: `{v}`")
            lines.append("")
        return "\n".join(lines)


def collect() -> DiagnosticsReport:
    """Collect diagnostic information synchronously.

    This function is safe to call from a thread; UI code should call it
    via ``GLib.Thread`` and then marshal back to the main thread.
    """
    report = DiagnosticsReport()

    sv = sys_version.detect()
    report.system = {
        "OS": sv.short(),
        "AnduinOS version": sv.anduinos_version or "n/a",
        "Base distro": sv.base_distro or "n/a",
        "Base version": sv.base_version or "n/a",
        "Kernel": sv.kernel or "n/a",
        "Architecture": platform.machine(),
        "Python": platform.python_version(),
        "Hostname": platform.node(),
    }
    report.desktop = {
        "Desktop": sv.desktop or "n/a",
        "Session type": sv.session_type or "n/a",
        "GNOME version": _gnome_version() or "n/a",
        "GTK version": _gtk_version() or "n/a",
    }
    report.hardware = {
        "CPU": _cpu_model(),
        "CPU cores": str(os.cpu_count() or 0),
        "RAM": _memory_total(),
        "GPU": _gpu_model() or "n/a",
    }
    report.network = {
        "Default interface": _default_iface() or "n/a",
        "IP address": _default_ip() or "n/a",
    }
    report.storage = {
        "Root filesystem": _disk_usage("/") or "n/a",
        "Home filesystem": _disk_usage(str(Path.home())) or "n/a",
    }
    report.package_managers = {
        "apt": shutil.which("apt") is not None,
        "dpkg": shutil.which("dpkg") is not None,
        "flatpak": shutil.which("flatpak") is not None,
        "snap": shutil.which("snap") is not None,
        "nix": shutil.which("nix") is not None,
        "apkg": shutil.which("apkg") is not None,
    }
    return report


# ----------------------------------------------------------------------
# individual collectors
# ----------------------------------------------------------------------
def _safe_read(path: str, limit: int = 8192) -> str:
    if path in _SENSITIVE_FILES:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _cpu_model() -> str:
    text = _safe_read(_SAFE_FILES["cpuinfo"], 32 * 1024)
    if not text:
        return platform.processor() or "Unknown"
    for line in text.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor() or "Unknown"


def _memory_total() -> str:
    text = _safe_read(_SAFE_FILES["meminfo"])
    if not text:
        return "Unknown"
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            kb = int(line.split()[1])
            gb = kb / 1024 / 1024
            return f"{gb:.1f} GiB"
    return "Unknown"


def _gpu_model() -> str | None:
    for cmd in (["lspci"],):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            if "vga compatible controller" in line.lower() or "3d controller" in line.lower():
                return line.split(":", 2)[-1].strip()
    return None


def _gnome_version() -> str | None:
    for cmd in (["gnome-shell", "--version"],):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            continue
    # Try reading from package manager or /usr/share/gnome-shell
    return None


def _gtk_version() -> str | None:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        major = Gtk.get_major_version()
        minor = Gtk.get_minor_version()
        micro = Gtk.get_micro_version()
        return f"{major}.{minor}.{micro}"
    except Exception:  # noqa: BLE001
        return None


def _default_iface() -> str | None:
    text = _safe_read("/proc/net/route")
    if not text:
        return None
    lines = text.splitlines()[1:]
    for line in lines:
        cols = line.split()
        if len(cols) >= 8 and cols[1] == "00000000":
            return cols[0]
    return None


def _default_ip() -> str | None:
    try:
        out = subprocess.run(
            ["ip", "-4", "route", "get", "1.1.1.1"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for token in out.split():
            if token.count(".") == 3 and token[0].isdigit():
                return token
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _disk_usage(path: str) -> str:
    try:
        total, used, free = shutil.disk_usage(path)
        return f"{used / 1024**3:.1f} / {total / 1024**3:.1f} GiB ({free / 1024**3:.1f} GiB free)"
    except OSError:
        return ""
