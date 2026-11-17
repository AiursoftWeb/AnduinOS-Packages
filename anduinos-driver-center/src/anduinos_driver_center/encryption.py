"""Read-only encryption coverage for the running system, not unlocked guests.

Mount UUIDs bind the view to the filesystems actually in use. Duplicate UUIDs,
missing block metadata and opaque storage layers stay unknown. No key material,
LUKS headers or authentication helpers are read by this page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re

from .core import Runner, SubprocessRunner
from .recovery_partition import active_swaps


@dataclass(frozen=True)
class VolumeState:
    device: str = ""
    filesystem: str = ""
    encrypted: bool | None = None
    memory: bool = False


@dataclass(frozen=True)
class EncryptionState:
    root: VolumeState = field(default_factory=VolumeState)
    home: VolumeState = field(default_factory=VolumeState)
    swaps: tuple[VolumeState, ...] = ()
    swap_known: bool = False

    @property
    def disk_swap(self) -> str:
        if not self.swap_known:
            return "unknown"
        disks = [item for item in self.swaps if not item.memory]
        if not disks:
            return "none"
        if any(item.encrypted is False for item in disks):
            return "plaintext"
        if all(item.encrypted is True for item in disks):
            return "encrypted"
        return "unknown"


def _block_state(block: dict, blocks: list[dict]) -> VolumeState:
    """Follow this device's actual parents, never a mapper-name convention."""
    device = block.get("name") or ""
    filesystem = block.get("fstype") or ""
    chain = []
    seen = set()
    current = block
    while current:
        name = os.path.realpath(current.get("name") or "")
        if name in seen:
            return VolumeState(device, filesystem)
        seen.add(name)
        chain.append(current.get("type"))
        if current.get("type") == "crypt":
            return VolumeState(device, filesystem, True)
        parent = current.get("pkname")
        if not parent:
            # A partition without its disk is incomplete evidence, not proof
            # of plaintext. LVM/RAID stacks are deliberately not inferred.
            plain = current.get("type") == "disk" and all(
                item in {"disk", "part"} for item in chain
            )
            return VolumeState(device, filesystem, False if plain else None)
        parent = parent if parent.startswith("/dev/") else "/dev/" + parent
        matches = [item for item in blocks
                   if os.path.realpath(item.get("name") or "") == os.path.realpath(parent)]
        if len(matches) != 1:
            return VolumeState(device, filesystem)
        current = matches[0]
    return VolumeState(device, filesystem)


def _mounted_volume(target: str, runner: Runner, blocks: list[dict]) -> VolumeState:
    result = runner.run(["findmnt", "--json", "--output", "SOURCE,FSTYPE,UUID",
                         "--target", target])
    if result.returncode:
        return VolumeState()
    try:
        mounts = json.loads(result.stdout)["filesystems"]
        if len(mounts) != 1:
            return VolumeState()
        mount = mounts[0]
        filesystem = mount.get("fstype") or ""
        source = mount.get("source") or ""
        identity = mount.get("uuid")
        if not identity or filesystem not in {"btrfs", "ext4"}:
            return VolumeState(source, filesystem)
        matches = [item for item in blocks
                   if item.get("uuid") == identity and item.get("fstype") == filesystem]
        # In particular, multiple Btrfs devices share a filesystem UUID. A
        # single SOURCE from findmnt cannot establish their complete coverage.
        if len(matches) != 1:
            return VolumeState(source, filesystem)
        return _block_state(matches[0], blocks)
    except (ValueError, KeyError, TypeError, AttributeError):
        return VolumeState()


def encryption_state(runner: Runner | None = None) -> EncryptionState:
    runner = runner or SubprocessRunner()
    blocks = []
    result = runner.run(["lsblk", "--json", "--paths", "--list", "--output",
                         "NAME,TYPE,FSTYPE,UUID,PKNAME"])
    try:
        if result.returncode == 0:
            value = json.loads(result.stdout)["blockdevices"]
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                blocks = value
    except (ValueError, KeyError, TypeError):
        pass
    root = _mounted_volume("/", runner, blocks)
    home = _mounted_volume("/home", runner, blocks)
    swaps = []
    try:
        # Reuse the existing /proc/swaps reader: swapon has no JSON API.
        entries = active_swaps()
        if not isinstance(entries, list):
            return EncryptionState(root, home)
        for item in entries:
            name = item["name"]
            if not isinstance(name, str) or not name.startswith("/"):
                return EncryptionState(root, home)
            if re.fullmatch(r"/dev/zram\d+", name):
                swaps.append(VolumeState(name, memory=True))
            elif item.get("type") == "file":
                swaps.append(_mounted_volume(name, runner, blocks))
            else:
                matches = [block for block in blocks
                           if os.path.realpath(block.get("name") or "") == os.path.realpath(name)]
                swaps.append(_block_state(matches[0], blocks)
                             if len(matches) == 1 else VolumeState(name))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return EncryptionState(root, home)
    return EncryptionState(root, home, tuple(swaps), True)
