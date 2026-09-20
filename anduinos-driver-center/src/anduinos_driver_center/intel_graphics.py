"""Intel graphics inventory and policy. No GTK, module loading, or writes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import fnmatch
import os
from pathlib import Path
import re
import subprocess

DRIVERS = ("i915", "xe")
PARAMETERS = ("enable_psr", "enable_fbc", "enable_dc", "enable_panel_replay")
PCI_ADDRESS = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
# Add only after hardware acceptance, keyed by exact kernel release and PCI ID.
# Module aliases alone do not demonstrate functional or supported hardware.
VALIDATED_SWITCHES: frozenset[tuple[str, str, str]] = frozenset()


def read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return ""


def run(command: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"}, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))


@dataclass(frozen=True)
class IntelDevice:
    address: str
    device_id: str
    model: str
    driver: str
    modalias: str
    internal_panel: bool


def discover(root: Path = Path("/"), runner=run) -> list[IntelDevice]:
    devices = []
    for path in sorted((root / "sys/bus/pci/devices").glob("*")):
        if not PCI_ADDRESS.fullmatch(path.name):
            continue
        if read(path / "vendor") != "0x8086" or not read(path / "class").startswith("0x03"):
            continue
        driver_path = path / "driver"
        driver = driver_path.resolve().name if driver_path.is_symlink() else ""
        result = runner(["lspci", "-s", path.name])
        description = result.stdout.strip().partition(": ")[2] if result.returncode == 0 else ""
        internal = any(
            read(connector / "status") == "connected"
            for connector in (path / "drm").glob("card*/card*-*")
            if "-eDP-" in connector.name or "-LVDS-" in connector.name
        )
        devices.append(IntelDevice(path.name, read(path / "device").removeprefix("0x"),
                                   description or "Intel Graphics", driver,
                                   read(path / "modalias"), internal))
    return devices


def module_parameters(driver: str, kernel: str, runner=run) -> set[str]:
    result = runner(["modinfo", "-k", kernel, "-p", driver])
    return {line.partition(":")[0] for line in result.stdout.splitlines()} if result.returncode == 0 else set()


def can_switch(device: IntelDevice, target: str, kernels: list[str], runner=run) -> bool:
    if target not in DRIVERS or not kernels:
        return False
    for kernel in kernels:
        if (kernel, device.device_id, target) not in VALIDATED_SWITCHES:
            return False
        result = runner(["modinfo", "-k", kernel, "-F", "alias", target])
        if result.returncode or not any(fnmatch.fnmatchcase(device.modalias, alias)
                                       for alias in result.stdout.splitlines()):
            return False
    return True


def graphics_log(output: str, addresses: set[str]) -> str:
    """Bounded, boot-local GPU evidence; no full journal or kernel command line."""
    lines = [line for line in output.splitlines()
             if re.search(r"\b(i915|xe)\s+[0-9a-f]{4}:", line)
             and any(address in line for address in addresses)]
    return "\n".join(lines[-160:])[-24000:]


def issues_from_log(log: str) -> list[str]:
    issues = []
    if re.search(r"\b(?:PSR|Panel Replay)\b.*(?:fail|error|timeout)|(?:fail|error|timeout).*\bPSR\b", log, re.I):
        issues.append("psr")
    if re.search(r"(?:firmware|GuC|HuC|DMC).*(?:fail|error|not found|missing)", log, re.I):
        issues.append("firmware")
    if re.search(r"GPU HANG|wedged|GPU.*(?:reset|timeout)", log, re.I):
        issues.append("hang")
    return issues


@dataclass
class IntelSnapshot:
    devices: list[IntelDevice] = field(default_factory=list)
    kernel: str = ""
    parameters: dict[str, list[str]] = field(default_factory=dict)
    loaded_values: dict[str, str] = field(default_factory=dict)
    log: str = ""
    log_available: bool = False
    firmware_package: str = ""
    issues: list[str] = field(default_factory=list)
    debug_status: dict[str, str] = field(default_factory=dict)
    boot_kernels: list[str] = field(default_factory=list)

    def report(self) -> dict:
        return asdict(self)


def debug_status(devices: list[IntelDevice], root: Path) -> dict[str, str]:
    """Read only named status files from an already mounted debugfs.

    The explicit detailed-status action supplies root access. Missing files,
    permissions or kernel interfaces remain unknown; never mount debugfs or
    expose tuning/error-capture files through the privileged helper.
    """
    evidence = {}
    for device in devices:
        for card in (root / f"sys/bus/pci/devices/{device.address}/drm").glob("card*"):
            if not re.fullmatch(r"card[0-9]+", card.name):
                continue
            base = root / "sys/kernel/debug/dri" / card.name.removeprefix("card")
            if device.address not in read(base / "name"):
                continue
            paths = [("PSR", base / "i915_edp_psr_status"), ("DMC", base / "i915_dmc_info")]
            paths.extend((f"PSR/{connector.name}", connector / "i915_psr_status")
                         for connector in base.glob("*-*") if re.fullmatch(r"(?:eDP|DP)-[0-9-]+", connector.name))
            for uc in list(base.glob("gt*/uc"))[:16] + list(base.glob("tile*/gt*/uc"))[:16]:
                for name, filename in (("GuC", "guc_info"), ("HuC", "huc_info")):
                    paths.append((f"{name}/{uc.relative_to(base)}", uc / filename))
            for label, path in paths:
                try:
                    with path.open() as stream:
                        value = stream.read(4096).strip()
                except (OSError, UnicodeError):
                    continue
                if value:
                    evidence[f"{device.address}/{label}"] = value
    return evidence


def inspect(root: Path = Path("/"), runner=run) -> IntelSnapshot:
    snapshot = IntelSnapshot(devices=discover(root, runner), kernel=os.uname().release)
    if not snapshot.devices:
        return snapshot
    from .intel_graphics_settings import installed_kernels
    snapshot.boot_kernels = installed_kernels(root)
    for driver in DRIVERS:
        supported = module_parameters(driver, snapshot.kernel, runner)
        for kernel in snapshot.boot_kernels:
            if kernel != snapshot.kernel:
                supported &= module_parameters(driver, kernel, runner)
        snapshot.parameters[driver] = sorted(supported)
        for parameter in PARAMETERS:
            value = read(root / f"sys/module/{driver}/parameters/{parameter}")
            if value:
                snapshot.loaded_values[f"{driver}.{parameter}"] = value
    result = runner(["journalctl", "-k", "-b", "--no-pager", "-o", "cat", "-n", "4000"])
    snapshot.log_available = result.returncode == 0 and bool(result.stdout.strip())
    snapshot.log = graphics_log(result.stdout, {device.address for device in snapshot.devices})
    snapshot.issues = issues_from_log(snapshot.log)
    snapshot.debug_status = debug_status(snapshot.devices, root)
    result = runner(["dpkg-query", "-W", "-f=${Version}", "linux-firmware"])
    if result.returncode == 0:
        snapshot.firmware_package = result.stdout.strip()
    return snapshot
