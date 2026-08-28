"""Read-only NTFS shrink inspection shared by the UI helper and executor."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum


MIB = 1024 * 1024
GIB = 1024 * MIB
NTFS_PROBE = "/usr/bin/ntfs-3g.probe"
NTFS_RESIZE = "/usr/sbin/ntfsresize"
_MINIMUM_RE = re.compile(r"You might resize at\s+([0-9]+)\s+bytes", re.I)
_MFT_RELOCATION_RE = re.compile(r"Relocate record\s+0(?::|\s)", re.I)


class NtfsResizeBlockReason(str, Enum):
    NONE = "none"
    BITLOCKER = "bitlocker"
    MOUNTED = "mounted"
    NOT_NTFS = "not-ntfs"
    HIBERNATED = "hibernated"
    UNCLEAN = "unclean"
    IN_USE = "in-use"
    INVALID = "invalid"
    INCONSISTENT = "inconsistent"
    PROBE_FAILED = "probe-failed"
    CHECK_FAILED = "check-failed"
    RANGE_UNAVAILABLE = "range-unavailable"
    INSUFFICIENT_SPACE = "insufficient-space"
    TARGET_OUT_OF_RANGE = "target-out-of-range"
    TARGET_REJECTED = "target-rejected"
    MFT_RELOCATION_UNSAFE = "mft-relocation-unsafe"


@dataclass(frozen=True)
class NtfsResizeInspection:
    device: str
    filesystem: str
    current_size_bytes: int
    minimum_size_bytes: int
    maximum_size_bytes: int
    block_reason: NtfsResizeBlockReason
    message: str
    probe_exit_code: int | None = None
    target_size_bytes: int | None = None

    @property
    def safe(self) -> bool:
        return self.block_reason is NtfsResizeBlockReason.NONE

    def to_dict(self) -> dict[str, object]:
        return {
            "device": self.device,
            "filesystem": self.filesystem,
            "current_size_bytes": self.current_size_bytes,
            "minimum_size_bytes": self.minimum_size_bytes,
            "maximum_size_bytes": self.maximum_size_bytes,
            "block_reason": self.block_reason.value,
            "message": self.message,
            "probe_exit_code": self.probe_exit_code,
            "target_size_bytes": self.target_size_bytes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, value: object) -> "NtfsResizeInspection":
        if not isinstance(value, dict):
            raise ValueError("NTFS inspection is not an object")
        expected = {
            "device",
            "filesystem",
            "current_size_bytes",
            "minimum_size_bytes",
            "maximum_size_bytes",
            "block_reason",
            "message",
            "probe_exit_code",
            "target_size_bytes",
        }
        if set(value) != expected:
            raise ValueError("NTFS inspection fields are invalid")
        integers = (
            "current_size_bytes",
            "minimum_size_bytes",
            "maximum_size_bytes",
        )
        if any(type(value[item]) is not int for item in integers):
            raise ValueError("NTFS inspection sizes are invalid")
        probe_exit_code = value["probe_exit_code"]
        target_size_bytes = value["target_size_bytes"]
        if probe_exit_code is not None and type(probe_exit_code) is not int:
            raise ValueError("NTFS inspection probe code is invalid")
        if target_size_bytes is not None and type(target_size_bytes) is not int:
            raise ValueError("NTFS inspection target size is invalid")
        device = value["device"]
        filesystem = value["filesystem"]
        message = value["message"]
        if not all(isinstance(item, str) for item in (device, filesystem, message)):
            raise ValueError("NTFS inspection text is invalid")
        return cls(
            device=device,
            filesystem=filesystem,
            current_size_bytes=value["current_size_bytes"],
            minimum_size_bytes=value["minimum_size_bytes"],
            maximum_size_bytes=value["maximum_size_bytes"],
            block_reason=NtfsResizeBlockReason(value["block_reason"]),
            message=message,
            probe_exit_code=probe_exit_code,
            target_size_bytes=target_size_bytes,
        )

    @classmethod
    def from_json(cls, value: str) -> "NtfsResizeInspection":
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("NTFS inspection returned invalid JSON") from error
        return cls.from_dict(decoded)


def inspect_ntfs_resize(
    device: str,
    current_size_bytes: int,
    *,
    filesystem: str = "ntfs",
    mounted: bool = False,
    target_size_bytes: int | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> NtfsResizeInspection:
    """Return a fail-closed, non-writing shrink assessment for one volume."""

    normalized_filesystem = filesystem.casefold()
    if normalized_filesystem == "bitlocker":
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.BITLOCKER,
            "BitLocker must be fully disabled and decrypted in Windows.",
            target_size_bytes=target_size_bytes,
        )
    if normalized_filesystem != "ntfs":
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.NOT_NTFS,
            "Only plain NTFS volumes can be shrunk in this release.",
            target_size_bytes=target_size_bytes,
        )
    if mounted:
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.MOUNTED,
            "The NTFS volume is mounted or otherwise active.",
            target_size_bytes=target_size_bytes,
        )
    if current_size_bytes <= 0:
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.RANGE_UNAVAILABLE,
            "The current NTFS partition size is unavailable.",
            target_size_bytes=target_size_bytes,
        )

    environment = dict(os.environ, LC_ALL="C", LANGUAGE="C")
    probe = _run_read_only(
        run,
        (NTFS_PROBE, "--readwrite", device),
        timeout=30,
        environment=environment,
    )
    probe_failure = _probe_failure(probe.returncode)
    if probe_failure is not None:
        reason, message = probe_failure
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            reason,
            message,
            probe_exit_code=probe.returncode,
            target_size_bytes=target_size_bytes,
        )

    check = _run_read_only(
        run,
        (NTFS_RESIZE, "--check", "--no-action", device),
        timeout=300,
        environment=environment,
    )
    if check.returncode != 0:
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.CHECK_FAILED,
            _diagnostic(check, "NTFS consistency checking failed."),
            probe_exit_code=probe.returncode,
            target_size_bytes=target_size_bytes,
        )

    info = _run_read_only(
        run,
        (NTFS_RESIZE, "--info", "--no-action", device),
        timeout=300,
        environment=environment,
    )
    if info.returncode != 0:
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.RANGE_UNAVAILABLE,
            _diagnostic(info, "NTFS could not report a safe shrink range."),
            probe_exit_code=probe.returncode,
            target_size_bytes=target_size_bytes,
        )
    match = _MINIMUM_RE.search(info.stdout + "\n" + info.stderr)
    if match is None:
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.RANGE_UNAVAILABLE,
            "NTFS returned no trustworthy minimum size.",
            probe_exit_code=probe.returncode,
            target_size_bytes=target_size_bytes,
        )

    reported_minimum = int(match.group(1))
    safe_minimum = min(
        current_size_bytes,
        max(
            reported_minimum * 125 // 100,
            reported_minimum + 2 * GIB,
        ),
    )
    minimum_size_bytes = _align_up(safe_minimum, MIB)
    maximum_size_bytes = _align_down(current_size_bytes - MIB, MIB)
    if minimum_size_bytes > maximum_size_bytes:
        return _blocked(
            device,
            filesystem,
            current_size_bytes,
            NtfsResizeBlockReason.INSUFFICIENT_SPACE,
            "The volume has no safely reclaimable space.",
            probe_exit_code=probe.returncode,
            minimum_size_bytes=minimum_size_bytes,
            maximum_size_bytes=maximum_size_bytes,
            target_size_bytes=target_size_bytes,
        )

    if target_size_bytes is not None:
        if (
            target_size_bytes % MIB
            or target_size_bytes < minimum_size_bytes
            or target_size_bytes > maximum_size_bytes
        ):
            return _blocked(
                device,
                filesystem,
                current_size_bytes,
                NtfsResizeBlockReason.TARGET_OUT_OF_RANGE,
                "The requested NTFS size is outside the freshly verified range.",
                probe_exit_code=probe.returncode,
                minimum_size_bytes=minimum_size_bytes,
                maximum_size_bytes=maximum_size_bytes,
                target_size_bytes=target_size_bytes,
            )
        dry_run = _run_read_only(
            run,
            (
                NTFS_RESIZE,
                "--no-action",
                "--verbose",
                "--size",
                str(target_size_bytes),
                device,
            ),
            timeout=1800,
            environment=environment,
            input_text="n\n",
        )
        output = dry_run.stdout + "\n" + dry_run.stderr
        if dry_run.returncode != 0:
            return _blocked(
                device,
                filesystem,
                current_size_bytes,
                NtfsResizeBlockReason.TARGET_REJECTED,
                _diagnostic(dry_run, "NTFS rejected the requested size."),
                probe_exit_code=probe.returncode,
                minimum_size_bytes=minimum_size_bytes,
                maximum_size_bytes=maximum_size_bytes,
                target_size_bytes=target_size_bytes,
            )
        if _MFT_RELOCATION_RE.search(output):
            return _blocked(
                device,
                filesystem,
                current_size_bytes,
                NtfsResizeBlockReason.MFT_RELOCATION_UNSAFE,
                "Shrinking to this size would relocate the NTFS MFT; this "
                "release refuses that unsafe upstream code path.",
                probe_exit_code=probe.returncode,
                minimum_size_bytes=minimum_size_bytes,
                maximum_size_bytes=maximum_size_bytes,
                target_size_bytes=target_size_bytes,
            )

    return NtfsResizeInspection(
        device=device,
        filesystem="ntfs",
        current_size_bytes=current_size_bytes,
        minimum_size_bytes=minimum_size_bytes,
        maximum_size_bytes=maximum_size_bytes,
        block_reason=NtfsResizeBlockReason.NONE,
        message="The NTFS volume passed every read-only shrink check.",
        probe_exit_code=probe.returncode,
        target_size_bytes=target_size_bytes,
    )


def inspect_ntfs_resize_with_runner(
    device: str,
    current_size_bytes: int,
    runner: object,
    *,
    filesystem: str = "ntfs",
    mounted: bool = False,
    target_size_bytes: int | None = None,
) -> NtfsResizeInspection:
    """Adapt the executor's logged CommandRunner to the read-only inspector."""

    def run(command: Sequence[str], **kwargs: object):
        input_text = kwargs.get("input")
        timeout = kwargs.get("timeout")
        environment = kwargs.get("env")
        return runner.run(
            command,
            input_text=input_text if isinstance(input_text, str) else None,
            timeout=timeout if isinstance(timeout, int) else None,
            check=False,
            environment=(
                environment if isinstance(environment, dict) else None
            ),
        )

    return inspect_ntfs_resize(
        device,
        current_size_bytes,
        filesystem=filesystem,
        mounted=mounted,
        target_size_bytes=target_size_bytes,
        run=run,
    )


def _run_read_only(
    run: Callable[..., subprocess.CompletedProcess[str]],
    command: Sequence[str],
    *,
    timeout: int,
    environment: dict[str, str],
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=environment,
            input=input_text,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(
            list(command),
            127,
            "",
            str(error),
        )


def _probe_failure(
    returncode: int,
) -> tuple[NtfsResizeBlockReason, str] | None:
    return {
        0: None,
        12: (
            NtfsResizeBlockReason.INVALID,
            "The selected volume is not valid NTFS.",
        ),
        13: (
            NtfsResizeBlockReason.INCONSISTENT,
            "NTFS is inconsistent; repair it with chkdsk in Windows.",
        ),
        14: (
            NtfsResizeBlockReason.HIBERNATED,
            "Windows hibernation or Fast Startup is active.",
        ),
        15: (
            NtfsResizeBlockReason.UNCLEAN,
            "Windows did not cleanly unmount this NTFS volume.",
        ),
        16: (
            NtfsResizeBlockReason.IN_USE,
            "The NTFS volume is already open or in use.",
        ),
    }.get(
        returncode,
        (
            NtfsResizeBlockReason.PROBE_FAILED,
            f"NTFS mountability probing failed with code {returncode}.",
        ),
    )


def _blocked(
    device: str,
    filesystem: str,
    current_size_bytes: int,
    reason: NtfsResizeBlockReason,
    message: str,
    *,
    probe_exit_code: int | None = None,
    minimum_size_bytes: int = 0,
    maximum_size_bytes: int = 0,
    target_size_bytes: int | None = None,
) -> NtfsResizeInspection:
    return NtfsResizeInspection(
        device=device,
        filesystem=filesystem.casefold(),
        current_size_bytes=current_size_bytes,
        minimum_size_bytes=minimum_size_bytes,
        maximum_size_bytes=maximum_size_bytes,
        block_reason=reason,
        message=message,
        probe_exit_code=probe_exit_code,
        target_size_bytes=target_size_bytes,
    )


def _diagnostic(
    result: subprocess.CompletedProcess[str],
    fallback: str,
) -> str:
    lines = [
        line.strip()
        for line in (result.stderr + "\n" + result.stdout).splitlines()
        if line.strip()
    ]
    return lines[-1] if lines else fallback


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _align_down(value: int, alignment: int) -> int:
    return value // alignment * alignment
