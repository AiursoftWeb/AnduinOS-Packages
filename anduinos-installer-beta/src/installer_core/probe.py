"""Read-only discovery of the machine that will execute an install plan."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model import Architecture, DiskIdentity, Firmware, SecureBoot


class ProbeError(RuntimeError):
    pass


SUPPORTED_WHOLE_DISK_RE = re.compile(
    r"^/dev/(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$"
)


@dataclass(frozen=True)
class PlatformProbe:
    architecture: Architecture
    firmware: Firmware
    secure_boot: SecureBoot


def probe_platform(
    *,
    machine: str | None = None,
    efi_path: Path = Path("/sys/firmware/efi"),
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> PlatformProbe:
    raw_arch = machine or platform.machine()
    architecture = {
        "x86_64": Architecture.AMD64,
        "amd64": Architecture.AMD64,
        "aarch64": Architecture.ARM64,
        "arm64": Architecture.ARM64,
    }.get(raw_arch.lower())
    if architecture is None:
        raise ProbeError(f"Unsupported architecture: {raw_arch}")

    if not efi_path.is_dir():
        if architecture is Architecture.ARM64:
            raise ProbeError("arm64 installation requires standards-based UEFI")
        return PlatformProbe(
            architecture, Firmware.BIOS, SecureBoot.NOT_APPLICABLE
        )

    try:
        result = run(
            ["mokutil", "--sb-state"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ProbeError(f"Cannot determine Secure Boot state: {error}") from error

    output = f"{result.stdout}\n{result.stderr}".lower()
    if "secureboot enabled" in output or "secure boot enabled" in output:
        secure_boot = SecureBoot.ENABLED
    elif "secureboot disabled" in output or "secure boot disabled" in output:
        secure_boot = SecureBoot.DISABLED
    else:
        raise ProbeError(
            "mokutil did not report an unambiguous Secure Boot state"
        )
    return PlatformProbe(architecture, Firmware.UEFI, secure_boot)


def probe_disks(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[DiskIdentity, ...]:
    """Return fixed whole disks with the strongest identity the system exposes.

    Physical disks normally provide a WWN, serial, or /dev/disk/by-id link.
    Serial-less virtual disks fall back to their attachment path and kernel
    device number, which is deliberately scoped to this Live boot. Install
    plans are never resumed across boots, and the executor re-probes this
    identity and the exact size immediately before destructive work.
    """
    try:
        result = run(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--nodeps",
                "--output",
                "PATH,SIZE,MODEL,SERIAL,WWN,TYPE,RM,MAJ:MIN",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ProbeError(f"Cannot enumerate disks: {error}") from error
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or "lsblk failed")

    try:
        devices = json.loads(result.stdout)["blockdevices"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ProbeError("lsblk returned invalid JSON") from error

    disks: list[DiskIdentity] = []
    for item in devices:
        if item.get("type") != "disk" or bool(item.get("rm")):
            continue
        path = str(item.get("path", ""))
        if not SUPPORTED_WHOLE_DISK_RE.fullmatch(path):
            continue
        stable_id = _stable_disk_id(
            path,
            str(item.get("wwn") or ""),
            str(item.get("serial") or ""),
            str(item.get("maj:min") or ""),
        )
        if not path or not stable_id:
            continue
        disks.append(
            DiskIdentity(
                path=path,
                stable_id=stable_id,
                expected_size_bytes=int(item.get("size") or 0),
                model=str(item.get("model") or "").strip(),
                serial=str(item.get("serial") or "").strip(),
            )
        )
    return tuple(disks)


def _stable_disk_id(
    path: str,
    wwn: str,
    serial: str,
    major_minor: str = "",
    *,
    by_id: Path = Path("/dev/disk/by-id"),
    by_path: Path = Path("/dev/disk/by-path"),
    sys_class_block: Path = Path("/sys/class/block"),
) -> str:
    if wwn.strip():
        return f"wwn:{wwn.strip()}"
    if serial.strip():
        return f"serial:{serial.strip()}"

    persistent = _matching_device_link(path, by_id)
    if persistent:
        return f"by-id:{persistent}"
    attachment = _matching_device_link(path, by_path)
    if attachment:
        return f"by-path:{attachment}"

    # Ubiquity/partman accepts the kernel whole-disk path directly. Keep that
    # compatibility for common serial-less QEMU/virtio disks, while binding
    # our immutable plan more tightly to the current Live session.
    device_name = Path(path).name
    sysfs_device = sys_class_block / device_name
    try:
        if sysfs_device.exists():
            resolved = sysfs_device.resolve(strict=True)
            return f"sysfs:{resolved}|dev:{major_minor.strip()}"
    except OSError:
        pass
    if path.startswith("/dev/") and major_minor.strip():
        return f"kernel:{path}|dev:{major_minor.strip()}"
    return ""


def _matching_device_link(path: str, directory: Path) -> str:
    if not directory.is_dir():
        return ""
    try:
        real_path = os.path.realpath(path)
        candidates = sorted(
            entry.name
            for entry in directory.iterdir()
            if "-part" not in entry.name
            and os.path.realpath(entry) == real_path
        )
    except OSError:
        return ""
    return candidates[0] if candidates else ""
