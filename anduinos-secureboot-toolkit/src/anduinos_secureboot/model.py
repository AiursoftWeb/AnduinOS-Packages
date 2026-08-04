"""Immutable state shared by every Secure Boot frontend."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    @property
    def trust_ready(self) -> bool:
        return not self.enabled or (
            self.key_present
            and self.certificate_present
            and self.enrolled
        )

    @property
    def ready(self) -> bool:
        return self.trust_ready and (
            not self.enabled or self.configuration_present
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
