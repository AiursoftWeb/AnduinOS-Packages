#!/usr/bin/python3
"""Polkit boundary for exact, read-only partition geometry discovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence

from installer_core.probe import SUPPORTED_WHOLE_DISK_RE


def _error(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main(
    arguments: Sequence[str] | None = None,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    geteuid: Callable[[], int] = os.geteuid,
) -> int:
    args = tuple(sys.argv[1:] if arguments is None else arguments)
    if geteuid() != 0:
        return _error("The storage probe must be authorized by Polkit.")
    if len(args) != 1 or not SUPPORTED_WHOLE_DISK_RE.fullmatch(args[0]):
        return _error("The storage probe accepts exactly one supported whole disk.")
    disk = args[0]
    environment = dict(os.environ, LC_ALL="C", LANGUAGE="C")

    try:
        identity = run(
            [
                "/usr/bin/lsblk",
                "--json",
                "--nodeps",
                "--paths",
                "--output",
                "PATH,TYPE,RM",
                disk,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return _error(f"Cannot validate the selected disk: {error}")
    if identity.returncode != 0:
        return _error(identity.stderr.strip() or "lsblk validation failed")
    try:
        devices = json.loads(identity.stdout)["blockdevices"]
        device = devices[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return _error("lsblk returned invalid disk identity data")
    if (
        len(devices) != 1
        or str(device.get("path") or "") != disk
        or str(device.get("type") or "") != "disk"
        or bool(device.get("rm"))
    ):
        return _error("The requested device is not a supported fixed whole disk")

    try:
        result = run(
            [
                "/usr/sbin/parted",
                "--machine",
                "--script",
                disk,
                "unit",
                "B",
                "print",
                "free",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return _error(f"Cannot read partition geometry: {error}")
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
