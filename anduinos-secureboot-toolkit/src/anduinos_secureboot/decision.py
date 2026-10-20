"""Pure page decisions. No GTK, system inspection, or privileged operations."""
from dataclasses import dataclass

from .model import DkmsState, SecureBootState, SecureBootStatus


@dataclass(frozen=True)
class NextBootEvidence:
    loader_source: str
    loader_path: str
    kernel_release: str
    firmware_trusted: bool | None
    loader_trusted: bool | None
    kernel_trusted: bool | None
    modules_trusted: bool | None


@dataclass(frozen=True)
class Facts:
    secure_boot: SecureBootState
    dkms: DkmsState
    drivers_checked: bool = False
    next_boot: NextBootEvidence | None = None


@dataclass(frozen=True)
class Blocker:
    reason: str
    row_id: str


@dataclass(frozen=True)
class ActionDecision:
    id: str
    blockers: tuple[Blocker, ...] = ()

    @property
    def allowed(self):
        return not self.blockers


@dataclass(frozen=True)
class RowDecision:
    id: str
    tone: str
    reason: str
    action: ActionDecision | None = None


@dataclass(frozen=True)
class PageDecision:
    rows: tuple[RowDecision, ...]
    next_row: str | None


def _broken_boot(facts):
    boot = facts.secure_boot.grub
    return (facts.secure_boot.enabled and boot is not None and boot.installed
            and boot.active_chain == "untrusted")


def assess_action(action_id: str, facts: Facts) -> ActionDecision:
    """Return prerequisites for an installed-system interactive action.

    First installation and package hooks retain their own transaction checks.
    This decision is not an authorization token: writers recheck local inputs.
    """
    if action_id not in {"prepare-identity", "regenerate-identity", "sync-defaults",
                         "install-bootloader", "repair-bootloader", "repair-dkms",
                         "reboot-enroll", "firmware-setup", "refresh",
                         "show-firmware-help", "show-boot-recovery"}:
        raise ValueError(f"Unsupported page action: {action_id}")
    state, identity = facts.secure_boot, facts.secure_boot.signing_identity
    blockers = []
    if action_id in {"prepare-identity", "regenerate-identity", "sync-defaults",
                     "install-bootloader", "repair-bootloader", "repair-dkms"}:
        if not state.supported:
            blockers.append(Blocker("firmware-unavailable", "secure_boot"))
    if action_id in {"install-bootloader", "repair-bootloader", "sync-defaults", "repair-dkms"}:
        if not identity or not identity.key_present or identity.key_valid is not True:
            blockers.append(Blocker("signing-key-unavailable", "certificate"))
        if not identity or identity.purpose not in {"unified", "module-only"}:
            blockers.append(Blocker("signing-certificate-unavailable", "certificate_purpose"))
        if not identity or identity.enrollment != "enrolled":
            blockers.append(Blocker("certificate-enrollment-required", "enrollment"))
    if action_id in {"install-bootloader", "repair-bootloader"}:
        if not identity or identity.purpose != "unified":
            blockers.append(Blocker("unified-certificate-required", "certificate_purpose"))
        if identity and identity.enrollment == "enrolled" and identity.defaults_ready is not True:
            blockers.append(Blocker("signing-defaults-required", "certificate"))
    if action_id == "repair-dkms":
        if not state.dkms_available or not state.headers_available:
            blockers.append(Blocker("driver-build-unavailable", "drivers"))
    if action_id == "reboot-enroll":
        boot = state.grub
        if state.status is SecureBootStatus.UNKNOWN:
            blockers.append(Blocker("firmware-unavailable", "secure_boot"))
        elif state.enabled and (boot is None or _broken_boot(facts)
                                or (boot.installed and boot.active_chain != "trusted")):
            blockers.append(Blocker("boot-recovery-required" if _broken_boot(facts)
                                    else "bootloader-unknown", "bootloader"))
    if action_id == "firmware-setup":
        evidence = facts.next_boot
        if state.status is not SecureBootStatus.DISABLED or state.setup_mode is not False:
            blockers.append(Blocker("firmware-unavailable", "secure_boot"))
        if not evidence or not evidence.loader_path or not evidence.kernel_release:
            blockers.append(Blocker("next-boot-unverified", "secure_boot"))
        else:
            if evidence.firmware_trusted is not True:
                blockers.append(Blocker("firmware-trust-unverified", "secure_boot"))
            boot = state.grub
            if (evidence.loader_trusted is not True or evidence.loader_source not in {"anduinos", "ubuntu"}
                    or (evidence.loader_source == "anduinos" and (not boot or not boot.ready))):
                blockers.append(Blocker("boot-chain-untrusted", "bootloader"))
            if evidence.kernel_trusted is not True:
                blockers.append(Blocker("next-kernel-unverified", "bootloader"))
            if evidence.modules_trusted is not True:
                blockers.append(Blocker("next-drivers-unverified", "drivers"))
    return ActionDecision(action_id, tuple(blockers))


def decide(facts: Facts) -> PageDecision:
    """Calculate all six rows together; old trust and a new identity are distinct."""
    state, identity, drivers, boot = (facts.secure_boot, facts.secure_boot.signing_identity,
                                     facts.dkms, facts.secure_boot.grub)
    def row(key, tone, reason, action=None):
        return RowDecision(key, tone, reason, assess_action(action, facts) if action else None)

    if state.status is SecureBootStatus.UNKNOWN:
        firmware = row("secure_boot", "warning", "firmware-unknown", "refresh")
    elif not state.supported:
        firmware = row("secure_boot", "neutral", "firmware-unsupported", "show-firmware-help")
    elif state.setup_mode is True:
        firmware = row("secure_boot", "warning", "platform-keys-missing", "show-firmware-help")
    elif state.enabled:
        firmware = row("secure_boot", "success", "secure-boot-enabled")
    else:
        firmware = row("secure_boot", "neutral", "secure-boot-disabled", "firmware-setup")

    if identity is None or identity.purpose == "unknown":
        certificate = row("certificate", "warning", "identity-unknown", "refresh")
    elif identity.purpose == "missing":
        certificate = row("certificate", "warning", "identity-missing", "prepare-identity")
    elif identity.key_valid is not True or not identity.key_present:
        certificate = row("certificate", "warning" if identity.key_valid is None else "error",
                          "signing-key-unavailable", "refresh" if identity.key_valid is None else "regenerate-identity")
    elif identity.usable and identity.defaults_ready is False:
        certificate = row("certificate", "warning", "signing-defaults-required", "sync-defaults")
    elif identity.usable and identity.defaults_ready is None:
        certificate = row("certificate", "warning", "signing-defaults-unknown", "refresh")
    else:
        certificate = row("certificate", "success", "identity-valid")

    purpose = identity.purpose if identity else "unknown"
    if purpose == "unified":
        permissions = row("certificate_purpose", "success", "certificate-unified")
    elif purpose == "module-only":
        permissions = row("certificate_purpose", "warning", "certificate-module-only", "prepare-identity")
    elif purpose in {"expired", "unsupported"}:
        permissions = row("certificate_purpose", "warning", "certificate-" + purpose, "regenerate-identity")
    else:
        permissions = row("certificate_purpose", "warning", "identity-" + purpose,
                          "prepare-identity" if purpose == "missing" else "refresh")

    enrollment = identity.enrollment if identity else "unknown"
    if not state.supported:
        trust = row("enrollment", "neutral", "firmware-unsupported", "show-firmware-help")
    elif enrollment == "enrolled":
        trust = row("enrollment", "success", "certificate-enrolled")
    elif purpose == "missing":
        trust = row("enrollment", "warning", "identity-missing", "prepare-identity")
    elif enrollment == "pending":
        trust = row("enrollment", "warning", "certificate-pending", "reboot-enroll")
    elif enrollment == "absent":
        trust = row("enrollment", "warning", "certificate-absent", "prepare-identity")
    else:
        trust = row("enrollment", "warning", "enrollment-unknown", "refresh")

    if not facts.drivers_checked:
        modules = row("drivers", "warning", "drivers-unknown", "refresh")
    elif not drivers.modules:
        modules = row("drivers", "neutral", "drivers-none")
    elif drivers.ready:
        modules = row("drivers", "success", "drivers-trusted")
    else:
        modules = row("drivers", "error" if state.enabled else "warning", "drivers-untrusted", "repair-dkms")

    if not state.supported or (boot is not None and not boot.supported):
        loader = row("bootloader", "neutral", "bootloader-unsupported", "show-firmware-help")
    elif _broken_boot(facts):
        repair = assess_action("repair-bootloader", facts)
        loader = row("bootloader", "error", "boot-recovery-required",
                     "repair-bootloader" if repair.allowed else "show-boot-recovery")
    elif boot is None:
        loader = row("bootloader", "warning", "bootloader-unknown", "refresh")
    elif not boot.installed:
        loader = row("bootloader", "warning", "bootloader-optional", "install-bootloader")
    elif boot.active_chain == "unknown":
        loader = row("bootloader", "warning", "bootloader-unknown", "refresh")
    elif boot.ready:
        loader = row("bootloader", "success", "bootloader-ready")
    else:
        reason = ("bootloader-unsigned" if boot.signature != "valid" else
                  "bootloader-unenrolled" if boot.enrollment != "enrolled" else "bootloader-undeployed")
        loader = row("bootloader", "warning", reason, "repair-bootloader")

    rows = (firmware, certificate, permissions, trust, modules, loader)
    # Prefer a real fault over optional migration. Follow prerequisites rather
    # than recommending a disabled downstream operation.
    priority = tuple(r for r in (loader, modules, certificate, permissions, trust, firmware)
                     if r.tone == "error") + tuple(
        r for r in (certificate, permissions, trust, modules, loader, firmware) if r.tone == "warning")
    next_row = None
    for candidate in priority:
        if candidate.tone not in {"error", "warning"}:
            continue
        next_row = candidate.id
        if candidate.action and candidate.action.blockers:
            next_row = candidate.action.blockers[0].row_id
        break
    return PageDecision(rows, next_row)
