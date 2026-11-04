"""LUKS unlock sessions for the existing, short-lived privileged helpers.

Only mapping metadata lives in /run. Passwords go straight to cryptsetup's
standard input and are never saved. A random session receipt distinguishes
our mappings from devices the user had already opened in another application.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from .i18n import _ as tr
from .storage import SystemIdentity, inspect_target, probe_inventory, resolve_target


RUNTIME = Path("/run/anduinos-rescue-center")


def _caller() -> int:
    value = os.environ.get("PKEXEC_UID", str(os.getuid()))
    if not value.isdigit():
        raise RuntimeError(tr("Could not identify the desktop user"))
    return int(value)


def _mapping(container, *, run):
    inventory = probe_inventory(run=run, inspect=lambda *_: SystemIdentity())
    matches = [part for disk in inventory.disks for part in disk.partitions
               if part.path == container.path or part.container_path == container.path]
    if len(matches) != 1:
        raise RuntimeError(tr("The selected partition is no longer available"))
    mapped = matches[0]
    expected = mapped.container_identity or mapped.identity
    if expected != container.identity:
        raise RuntimeError(tr("The selected partition changed after it was scanned"))
    if mapped.probe_error:
        raise RuntimeError(mapped.probe_error)
    return mapped


def unlock_volume(path: str, identity: str, password: str, *,
                  run=subprocess.run, runtime: Path = RUNTIME) -> dict:
    """Open one LUKS2 partition, then positively identify the inner system."""
    if not password or len(password) > 4096 or "\0" in password or "\n" in password:
        raise ValueError(tr("The disk password is empty or unsupported"))
    container = resolve_target(path, identity, run=run)
    if container.filesystem != "crypto_luks" or container.active_system:
        raise RuntimeError(tr("Select an offline encrypted volume"))
    result = run(["cryptsetup", "isLuks", "--type", "luks2", "--", path],
                 capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError(tr("Only LUKS2 volumes are supported"))
    mapped = _mapping(container, run=run)
    if mapped.container_path:
        # This device belongs to the user. Never create a cleanup receipt for it.
        payload = inspect_target(mapped.path, mapped.identity, run=run, mount_base=runtime)
        payload["unlock_session"] = ""
        return payload
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = runtime.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_mode & 0o077):
        raise RuntimeError(tr("The recovery runtime directory is unsafe"))
    session = uuid4().hex
    name = "anduinos-rescue-" + session
    receipt = runtime / (session + ".json")
    opened = False
    try:
        result = run(["cryptsetup", "open", "--type", "luks2", "--key-file", "-",
                      "--", path, name], input=password, capture_output=True,
                     text=True, timeout=180, check=False)
        if result.returncode:
            raise RuntimeError(tr("Could not unlock the volume. Check the password or recovery key."))
        opened = True
        result = run(["udevadm", "settle", "--timeout=10"], capture_output=True,
                     text=True, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError(tr("The unlocked device is not ready"))
        mapped = _mapping(container, run=run)
        if mapped.path != "/dev/mapper/" + name or not mapped.container_path:
            raise RuntimeError(tr("The unlocked device does not match the selected volume"))
        data = {"path": mapped.path, "identity": mapped.identity, "uid": _caller()}
        with receipt.open("x", encoding="utf-8") as stream:
            os.chmod(receipt, 0o600)
            json.dump(data, stream)
        payload = inspect_target(mapped.path, mapped.identity, run=run, mount_base=runtime)
        payload["unlock_session"] = session
        return payload
    except BaseException:
        # Also attempt cleanup if cryptsetup timed out after creating its device.
        # The random name was generated here; no existing mapping can be adopted.
        result = run(["cryptsetup", "close", "--", name], capture_output=True,
                     text=True, timeout=30, check=False)
        if result.returncode and opened:
            raise RuntimeError(tr("Could not close the unlocked volume; it may still be in use")) from None
        receipt.unlink(missing_ok=True)
        raise


def lock_volume(session: str, *, run=subprocess.run, runtime: Path = RUNTIME) -> dict:
    """Close only a caller-owned mapping whose current identity still matches."""
    if UUID(session).hex != session:
        raise ValueError(tr("Invalid unlock session"))
    receipt = runtime / (session + ".json")
    metadata = receipt.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise RuntimeError(tr("Invalid unlock session"))
    data = json.loads(receipt.read_text())
    if data["uid"] != _caller() or data["path"] != "/dev/mapper/anduinos-rescue-" + session:
        raise RuntimeError(tr("Invalid unlock session"))
    partition = resolve_target(data["path"], data["identity"], run=run)
    if not partition.encrypted or partition.active_system or partition.mountpoints:
        raise RuntimeError(tr("Unmount the unlocked volume before closing it"))
    result = run(["cryptsetup", "close", "--", partition.path], capture_output=True,
                 text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(tr("Could not close the unlocked volume; it may still be in use"))
    receipt.unlink()
    return {"schema": 1, "closed": True}
