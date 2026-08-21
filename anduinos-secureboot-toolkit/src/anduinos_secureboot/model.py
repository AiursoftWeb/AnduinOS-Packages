"""Immutable state shared by every Secure Boot frontend."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SecureBootStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SecureBootState:
    enabled: bool
    key_present: bool
    certificate_present: bool
    enrolled: bool
    certificate_serial: str | None
    enrollment_pending: bool = False
    dkms_available: bool = False
    headers_available: bool = False
    configuration_present: bool = True
    status: SecureBootStatus | None = None

    def __post_init__(self) -> None:
        status = self.status
        if status is None:
            status = (
                SecureBootStatus.ENABLED
                if self.enabled
                else SecureBootStatus.DISABLED
            )
            object.__setattr__(self, "status", status)
        if self.enabled is not (status is SecureBootStatus.ENABLED):
            raise ValueError("enabled and status describe different states")

    @property
    def supported(self) -> bool:
        return self.status in {
            SecureBootStatus.ENABLED,
            SecureBootStatus.DISABLED,
        }

    @property
    def state_known(self) -> bool:
        return self.status is not SecureBootStatus.UNKNOWN

    @property
    def enforcement_inactive(self) -> bool:
        return self.status in {
            SecureBootStatus.DISABLED,
            SecureBootStatus.UNSUPPORTED,
        }

    @property
    def trust_ready(self) -> bool:
        return self.enforcement_inactive or (
            self.status is SecureBootStatus.ENABLED
            and self.key_present
            and self.certificate_present
            and self.enrolled
        )

    @property
    def ready(self) -> bool:
        return self.trust_ready and (
            self.enforcement_inactive or self.configuration_present
        )

    @property
    def enrollment_required(self) -> bool:
        return self.enabled and not self.trust_ready and not self.enrollment_pending


@dataclass(frozen=True)
class ModuleState:
    name: str
    path: str
    signature_key: str | None
    trusted: bool


@dataclass(frozen=True)
class DkmsState:
    modules: tuple[str, ...] = field(default_factory=tuple)
    trusted_modules: tuple[str, ...] = field(default_factory=tuple)
    untrusted_modules: tuple[str, ...] = field(default_factory=tuple)
    details: tuple[ModuleState, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return not self.untrusted_modules
