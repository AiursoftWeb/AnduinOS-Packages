"""Read-only discovery of the machine that will execute an install plan."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model import Architecture, DiskIdentity, Firmware, SecureBoot


class ProbeError(RuntimeError):
    pass


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
    """Return whole, non-removable disks with an identity stable across boots."""
    try:
        result = run(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--nodeps",
                "--output",
                "PATH,SIZE,MODEL,SERIAL,WWN,TYPE,RM",
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
        stable_id = _stable_disk_id(
            path, str(item.get("wwn") or ""), str(item.get("serial") or "")
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


def _stable_disk_id(path: str, wwn: str, serial: str) -> str:
    if wwn.strip():
        return f"wwn:{wwn.strip()}"
    if serial.strip():
        return f"serial:{serial.strip()}"

    by_id = Path("/dev/disk/by-id")
    if not by_id.is_dir():
        return ""
    try:
        real_path = os.path.realpath(path)
        candidates = sorted(
            entry.name
            for entry in by_id.iterdir()
            if "-part" not in entry.name
            and os.path.realpath(entry) == real_path
        )
    except OSError:
        return ""
    return f"by-id:{candidates[0]}" if candidates else ""

