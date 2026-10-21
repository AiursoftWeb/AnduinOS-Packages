"""Offline ext4 shrink: read-only assessment, then explicitly authorized writes.

The minimum from resize2fs is an estimate, not a dry run. Keep headroom and
verify the actual filesystem geometry before allowing a partition-tail edit.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from .resize import ResizeBlockReason, ResizeInspection
from .ntfs_resize import MIB, GIB, _run_read_only

E2FSCK = "/usr/sbin/e2fsck"
RESIZE2FS = "/usr/sbin/resize2fs"
DUMPE2FS = "/usr/sbin/dumpe2fs"
# Deliberately small first-release surface: ordinary 4 KiB ext4, internal journal.
SUPPORTED_FEATURES = frozenset("has_journal ext_attr resize_inode dir_index filetype "
    "extent 64bit flex_bg sparse_super sparse_super2 large_file huge_file dir_nlink "
    "extra_isize metadata_csum metadata_csum_seed uninit_bg orphan_file".split())


@dataclass(frozen=True)
class Ext4Geometry:
    uuid: str
    block_size: int
    block_count: int
    free_blocks: int

    @property
    def size_bytes(self) -> int:
        return self.block_size * self.block_count


def parse_ext4_geometry(output: str) -> Ext4Geometry:
    fields = {}
    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            if key in fields:
                raise ValueError("Ambiguous ext4 superblock fields")
            fields[key] = value.strip()
    features = set(fields.get("Filesystem features", "").split())
    if (fields.get("Filesystem magic number") != "0xEF53"
            or fields.get("Filesystem state") != "clean"
            or not {"extent", "has_journal"} <= features
            or features - SUPPORTED_FEATURES
            or "Journal device" in fields):
        raise ValueError("ext4 is not clean or uses unsupported filesystem features")
    try:
        geometry = Ext4Geometry(fields["Filesystem UUID"], int(fields["Block size"]),
            int(fields["Block count"]), int(fields["Free blocks"]))
    except (KeyError, ValueError) as error:
        raise ValueError("Invalid ext4 superblock geometry") from error
    if (not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", geometry.uuid)
            or geometry.block_size != 4096
            or geometry.block_count <= 0
            or not 0 <= geometry.free_blocks <= geometry.block_count):
        raise ValueError("Only ordinary ext4 with 4 KiB blocks is supported for shrinking")
    return geometry


def inspect_ext4_resize(device: str, current_size_bytes: int, *,
                        filesystem: str = "ext4", mounted: bool = False,
                        target_size_bytes: int | None = None,
                        run=subprocess.run) -> ResizeInspection:
    """Never repair, mount or resize during probing (including failed probes)."""
    def blocked(reason, message, minimum=0, maximum=0):
        return ResizeInspection(device, filesystem.casefold(), current_size_bytes,
            minimum, maximum, reason, message, target_size_bytes=target_size_bytes)

    if filesystem.casefold() != "ext4":
        return blocked(ResizeBlockReason.NOT_EXT4, "The selected filesystem is not ext4.")
    if mounted:
        return blocked(ResizeBlockReason.MOUNTED, "The ext4 filesystem is mounted or active.")
    if current_size_bytes <= 0:
        return blocked(ResizeBlockReason.RANGE_UNAVAILABLE, "Partition size is unavailable.")
    environment = dict(os.environ, LC_ALL="C", LANGUAGE="C")
    def read(command):
        return _run_read_only(run, command, timeout=1800, environment=environment,
                              input_text="")
    header = read((DUMPE2FS, "-h", device))
    if header.returncode:
        return blocked(ResizeBlockReason.PROBE_FAILED, "Cannot read the ext4 superblock.")
    try:
        geometry = parse_ext4_geometry(header.stdout)
    except ValueError as error:
        return blocked(ResizeBlockReason.UNSUPPORTED, str(error))
    if geometry.size_bytes > current_size_bytes:
        return blocked(ResizeBlockReason.INVALID, "ext4 extends beyond its partition.")
    check = read((E2FSCK, "-f", "-n", device))
    if check.returncode != 0:
        return blocked(ResizeBlockReason.CHECK_FAILED,
                       "ext4 needs repair or could not be checked; repair it offline first.")
    estimate = read((RESIZE2FS, "-P", device))
    matches = re.findall(r"^Estimated minimum size of the filesystem:\s*([0-9]+)\s*$",
                         estimate.stdout, re.M)
    if estimate.returncode or len(matches) != 1:
        return blocked(ResizeBlockReason.RANGE_UNAVAILABLE, "ext4 returned no usable size estimate.")
    blocks = int(matches[0])
    if not 0 < blocks <= geometry.block_count:
        return blocked(ResizeBlockReason.RANGE_UNAVAILABLE, "ext4 returned an invalid size estimate.")
    used = max(blocks, geometry.block_count - geometry.free_blocks) * geometry.block_size
    minimum = max(4 * GIB, used * 125 // 100, used + 2 * GIB)
    minimum = (minimum + MIB - 1) // MIB * MIB
    maximum = (current_size_bytes - MIB) // MIB * MIB
    if minimum > maximum:
        return blocked(ResizeBlockReason.INSUFFICIENT_SPACE,
                       "The ext4 filesystem has no safely reclaimable space.", minimum, maximum)
    if target_size_bytes is not None and (target_size_bytes % MIB
            or not minimum <= target_size_bytes <= maximum):
        return blocked(ResizeBlockReason.TARGET_OUT_OF_RANGE,
                       "The requested ext4 size is outside the freshly checked range.", minimum, maximum)
    return ResizeInspection(device, "ext4", current_size_bytes, minimum, maximum,
        ResizeBlockReason.NONE, "ext4 passed read-only checks; the minimum is a conservative estimate.",
        target_size_bytes=target_size_bytes)


def _runner_adapter(runner):
    def run(command, **options):
        return runner.run(command, input_text=options.get("input"),
            environment=options.get("env"), timeout=options.get("timeout"), check=False)
    return run


def inspect_ext4_resize_with_runner(device, current_size_bytes, runner, **options):
    return inspect_ext4_resize(device, current_size_bytes,
                               run=_runner_adapter(runner), **options)


def shrink_ext4_filesystem(device: str, target_size_bytes: int, runner,
                           *, expected_uuid: str | None = None) -> None:
    """Called only inside the executor's destructive filesystem boundary.

    Does not change the partition. Any failure leaves its old, larger boundary.
    """
    if target_size_bytes <= 0 or target_size_bytes % MIB:
        raise ValueError("ext4 shrink size must be positive and MiB-aligned")
    environment = dict(os.environ, LC_ALL="C", LANGUAGE="C")
    def command(argv, *, check=True):
        return runner.run(argv, input_text="", timeout=7200,
                          environment=environment, check=check)
    before = parse_ext4_geometry(command((DUMPE2FS, "-h", device)).stdout)
    if expected_uuid is not None and before.uuid.lower() != expected_uuid.lower():
        raise RuntimeError("ext4 UUID changed before resizing; partition unchanged")
    # -p performs only repairs e2fsck considers safe, never unconditional -y.
    checked = command((E2FSCK, "-f", "-p", device), check=False)
    if checked.returncode not in (0, 1):
        raise RuntimeError("ext4 check failed or requires reboot/manual repair; partition unchanged")
    if before.size_bytes > target_size_bytes:
        command((RESIZE2FS, device, f"{target_size_bytes // MIB}M"))
    command(("sync",))
    verified = command((E2FSCK, "-f", "-n", device), check=False)
    if verified.returncode != 0:
        raise RuntimeError("ext4 post-resize check failed; partition unchanged")
    after = parse_ext4_geometry(command((DUMPE2FS, "-h", device)).stdout)
    if (after.uuid != before.uuid or after.block_size != before.block_size
            or after.size_bytes > target_size_bytes):
        raise RuntimeError("ext4 post-resize geometry/identity mismatch; partition unchanged")
