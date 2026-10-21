"""Shared, command-free shrink assessment for the UI and executor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum


class ResizeBlockReason(str, Enum):
    NONE = "none"
    BITLOCKER = "bitlocker"
    MOUNTED = "mounted"
    NOT_NTFS = "not-ntfs"
    UNSUPPORTED = "unsupported"
    NOT_EXT4 = "not-ext4"
    HIBERNATED = "hibernated"
    UNCLEAN = "unclean"
    IN_USE = "in-use"
    INVALID = "invalid"
    INCONSISTENT = "inconsistent"
    PROBE_FAILED = "probe-failed"
    CHECK_FAILED = "check-failed"
    CHECK_REQUIRED = "check-required"
    RANGE_UNAVAILABLE = "range-unavailable"
    INSUFFICIENT_SPACE = "insufficient-space"
    TARGET_OUT_OF_RANGE = "target-out-of-range"
    TARGET_REJECTED = "target-rejected"
    MFT_RELOCATION_UNSAFE = "mft-relocation-unsafe"


@dataclass(frozen=True)
class ResizeInspection:
    device: str
    filesystem: str
    current_size_bytes: int
    minimum_size_bytes: int
    maximum_size_bytes: int
    block_reason: ResizeBlockReason
    message: str
    probe_exit_code: int | None = None
    target_size_bytes: int | None = None

    @property
    def safe(self) -> bool:
        return self.block_reason is ResizeBlockReason.NONE

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
    def from_dict(cls, value: object) -> "ResizeInspection":
        if not isinstance(value, dict):
            raise ValueError("Resize inspection is not an object")
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
            raise ValueError("Resize inspection fields are invalid")
        integers = (
            "current_size_bytes",
            "minimum_size_bytes",
            "maximum_size_bytes",
        )
        if any(type(value[item]) is not int for item in integers):
            raise ValueError("Resize inspection sizes are invalid")
        probe_exit_code = value["probe_exit_code"]
        target_size_bytes = value["target_size_bytes"]
        if probe_exit_code is not None and type(probe_exit_code) is not int:
            raise ValueError("Resize inspection probe code is invalid")
        if target_size_bytes is not None and type(target_size_bytes) is not int:
            raise ValueError("Resize inspection target size is invalid")
        device = value["device"]
        filesystem = value["filesystem"]
        message = value["message"]
        if not all(isinstance(item, str) for item in (device, filesystem, message)):
            raise ValueError("Resize inspection text is invalid")
        return cls(
            device=device,
            filesystem=filesystem,
            current_size_bytes=value["current_size_bytes"],
            minimum_size_bytes=value["minimum_size_bytes"],
            maximum_size_bytes=value["maximum_size_bytes"],
            block_reason=ResizeBlockReason(value["block_reason"]),
            message=message,
            probe_exit_code=probe_exit_code,
            target_size_bytes=target_size_bytes,
        )

    @classmethod
    def from_json(cls, value: str) -> "ResizeInspection":
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Resize inspection returned invalid JSON") from error
        return cls.from_dict(decoded)
