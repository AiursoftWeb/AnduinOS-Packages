#!/usr/bin/python3
"""Polkit boundary for exact, read-only partition geometry discovery."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence

from installer_core.ntfs_resize import inspect_ntfs_resize
from installer_core.probe import SUPPORTED_WHOLE_DISK_RE


SUPPORTED_PARTITION_RE = re.compile(
    r"^/dev/(?:sd[a-z]+[0-9]+|vd[a-z]+[0-9]+|xvd[a-z]+[0-9]+|"
    r"nvme[0-9]+n[0-9]+p[0-9]+|mmcblk[0-9]+p[0-9]+)$"
)


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
    if len(args) == 2 and args[0] == "--ntfs-inspect":
        return _inspect_ntfs(args[1], run=run)
    if len(args) != 1 or not SUPPORTED_WHOLE_DISK_RE.fullmatch(args[0]):
        return _error(
            "The storage probe accepts one supported whole disk or one "
            "explicit NTFS inspection."
        )
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


def _inspect_ntfs(
    partition: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> int:
    if not SUPPORTED_PARTITION_RE.fullmatch(partition):
        return _error("The NTFS probe requires one supported partition path.")
    environment = dict(os.environ, LC_ALL="C", LANGUAGE="C")
    try:
        identity = run(
            [
                "/usr/bin/lsblk",
                "--json",
                "--bytes",
                "--paths",
                "--nodeps",
                "--output",
                "PATH,SIZE,TYPE,RM,FSTYPE,PARTUUID,MOUNTPOINTS",
                partition,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return _error(f"Cannot validate the selected partition: {error}")
    if identity.returncode != 0:
        return _error(identity.stderr.strip() or "lsblk validation failed")
    try:
        devices = json.loads(identity.stdout)["blockdevices"]
        device = devices[0]
        mountpoints = device.get("mountpoints") or []
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return _error("lsblk returned invalid partition identity data")
    if not isinstance(mountpoints, list):
        mountpoints = [mountpoints]
    if (
        len(devices) != 1
        or str(device.get("path") or "") != partition
        or str(device.get("type") or "") != "part"
        or bool(device.get("rm"))
        or not str(device.get("partuuid") or "")
    ):
        return _error("The requested device is not a supported fixed partition")
    try:
        size_bytes = int(device.get("size") or 0)
    except (TypeError, ValueError):
        return _error("lsblk returned an invalid partition size")
    inspection = inspect_ntfs_resize(
        partition,
        size_bytes,
        filesystem=str(device.get("fstype") or ""),
        mounted=any(item for item in mountpoints),
        run=run,
    )
    print(inspection.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
