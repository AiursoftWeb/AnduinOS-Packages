"""Staged, additive certificate enrollment. Never modifies an ESP or a module.

Old enrolled identities and installed module signatures remain in place.
Signing defaults converge automatically only after successful enrollment.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

ROOT = Path('/var/lib/anduinos-secureboot')
LEGACY_CERTIFICATE = Path('/var/lib/shim-signed/mok/MOK.der')
LEGACY_KEY = Path('/var/lib/shim-signed/mok/MOK.priv')
MODULE_ONLY_OID = '1.3.6.1.4.1.2312.16.1.2'


@dataclass(frozen=True)
class IdentityStatus:
    certificate: str = ''
    purpose: str = 'unknown'
    enrollment: str = 'unknown'
    fingerprint: str = ''
    managed: bool = False
    detail: str = ''
    key_present: bool = False
    key_valid: bool | None = None
    defaults_ready: bool | None = None

    @property
    def usable(self) -> bool:
        return (self.purpose == 'unified' and self.enrollment == 'enrolled'
                and self.key_present and self.key_valid is True)

    # Read-only compatibility for an older Driver Center during package updates.
    # These are derived from enrollment, never a separate activation state.
    @property
    def candidate(self) -> bool:
        return self.managed and not self.usable

    @property
    def active(self) -> bool:
        return self.managed and self.usable


def identity_paths(root: Path, name: str) -> tuple[Path, Path]:
    if not re.fullmatch(r'[0-9a-f]{32}', name):
        raise ValueError('Invalid signing identity identifier')
    return root / 'identities' / name / 'MOK.priv', root / 'identities' / name / 'MOK.der'


def pointer(root: Path, name: str) -> str | None:
    path = root / (name + '.json')
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    value = data['identity']
    identity_paths(root, value)
    return value


def current_identity(root: Path) -> str | None:
    # Read earlier local-preview metadata too; no activation or migration write
    # is needed merely to discover that its certificate is now enrolled.
    return pointer(root, 'current') or pointer(root, 'candidate') or pointer(root, 'active')


def selected_paths(root: Path = ROOT, runner=None, state: IdentityStatus | None = None) -> tuple[Path, Path]:
    from .inspect import SubprocessRunner
    runner = runner or SubprocessRunner()
    state = state or inspect_identity(runner, root)
    name = current_identity(root)
    if name and state.usable:
        return identity_paths(root, name)
    previous = pointer(root, 'previous') or pointer(root, 'active')
    if previous and previous != name:
        key, cert = identity_paths(root, previous)
        if key.is_file() and enrollment_state(cert, runner) == 'enrolled':
            return key, cert
    return LEGACY_KEY, LEGACY_CERTIFICATE


def enrollment_state(certificate: Path, runner) -> str:
    from .inspect import _certificate_fingerprint, _listed_sha1_fingerprints
    fingerprint = _certificate_fingerprint(certificate, runner)
    if not fingerprint:
        return 'unknown'
    for command, state in (('--list-enrolled', 'enrolled'), ('--list-new', 'pending')):
        result = runner.run(['mokutil', command])
        values = _listed_sha1_fingerprints(result.stdout)
        if result.returncode or (result.stdout.strip() and not values):
            return 'unknown'
        if fingerprint in values:
            return state
    return 'absent'


def certificate_purpose(text: str) -> str:
    """Shared purpose policy for installed identities and fresh installations."""
    if MODULE_ONLY_OID in text:
        return 'module-only'
    eku = re.search(r'X509v3 Extended Key Usage[^\n]*\n\s*([^\n]+)', text)
    purpose = 'unified' if (eku is None or 'Code Signing' in eku[1]
                            or '1.3.6.1.5.5.7.3.3' in eku[1]) else 'unsupported'
    usage = re.search(r'X509v3 Key Usage[^\n]*\n\s*([^\n]+)', text)
    if usage and 'Digital Signature' not in usage[1]:
        purpose = 'unsupported'
    return purpose


def check_unified_pair(private_key: Path, certificate: Path, run) -> None:
    """Validate signing material without requiring firmware access/enrollment."""
    check_pair(private_key, certificate, run)
    text = run(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate), '-noout', '-text'], timeout=30)
    if text.returncode or not text.stdout.strip() or certificate_purpose(text.stdout) != 'unified':
        raise ValueError('Certificate must allow both GRUB and kernel module signing')
    if run(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate), '-noout', '-checkend', '0'], timeout=30).returncode:
        raise ValueError('Signing certificate has expired')


def inspect_identity(runner, root: Path = ROOT, *, privileged=False) -> IdentityStatus:
    # Root-owned private keys stay private. The existing fixed read-only probe
    # returns status only; it accepts no caller-selected paths.
    if root == ROOT and os.geteuid() != 0 and not privileged:
        result = runner.run(['pkexec', '/usr/libexec/anduinos-grub-probe', 'identity'], timeout=60)
        try:
            if result.returncode:
                raise ValueError(result.stderr.strip() or 'Signing identity inspection was not authorized')
            return IdentityStatus(**json.loads(result.stdout))
        except (ValueError, TypeError) as error:
            return IdentityStatus(detail=str(error))
    try:
        name = current_identity(root)
        key, certificate = identity_paths(root, name) if name else (LEGACY_KEY, LEGACY_CERTIFICATE)
        if not certificate.is_file():
            return IdentityStatus(certificate=str(certificate), purpose='missing',
                                  managed=bool(name))
        result = runner.run(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate), '-noout', '-text'])
        if result.returncode:
            raise ValueError('The certificate cannot be parsed')
        purpose = certificate_purpose(result.stdout)
        if runner.run(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate), '-noout', '-checkend', '0']).returncode:
            purpose = 'expired'
        key_valid, detail = True, ''
        try:
            check_pair(key, certificate, runner.run)
        except (OSError, ValueError) as error:
            key_valid, detail = False, str(error)
        from .defaults import defaults_ready
        return IdentityStatus(
            certificate=str(certificate), purpose=purpose,
            enrollment=enrollment_state(certificate, runner),
            fingerprint=hashlib.sha256(certificate.read_bytes()).hexdigest(),
            managed=bool(name),
            key_present=key.is_file(),
            key_valid=key_valid, detail=detail,
            defaults_ready=defaults_ready(key, certificate),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        return IdentityStatus(detail=str(error))


def check_pair(private_key: Path, certificate: Path, run) -> None:
    if not private_key.is_file() or not certificate.is_file():
        raise ValueError('Signing key or certificate is missing. Existing certificates were not replaced.')
    if private_key.is_symlink() or certificate.is_symlink():
        raise ValueError('Signing identity must not use redirected files')
    if private_key.stat().st_mode & 0o077:
        raise ValueError('Signing private key must only be accessible by its owner')
    key = run(['openssl', 'pkey', '-in', str(private_key), '-pubout'], timeout=30)
    cert = run(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate), '-pubkey', '-noout'], timeout=30)
    if key.returncode or cert.returncode or not key.stdout.strip() or key.stdout.strip() != cert.stdout.strip():
        raise ValueError('Signing key and certificate do not match. Use explicit certificate recovery.')


def generate_identity(private_key: Path, certificate: Path, run) -> None:
    """Explicit initialization only; never overwrite a partial existing pair."""
    if private_key.exists() or certificate.exists():
        check_pair(private_key, certificate, run)
        return
    private_key.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with tempfile.TemporaryDirectory(prefix='.generate-', dir=private_key.parent) as temporary:
        key = Path(temporary) / 'key'
        cert = Path(temporary) / 'cert'
        result = run([
            'openssl', 'req', '-new', '-x509', '-nodes', '-newkey', 'rsa:3072',
            '-days', '3650', '-subj', '/CN=AnduinOS Unified Boot and Module Signing',
            '-addext', 'basicConstraints=critical,CA:FALSE',
            '-addext', 'keyUsage=critical,digitalSignature',
            '-addext', 'extendedKeyUsage=codeSigning',
            '-outform', 'DER', '-keyout', str(key), '-out', str(cert),
        ], timeout=120)
        if result.returncode:
            raise ValueError('Could not generate the unified signing identity')
        key.chmod(0o600)
        cert.chmod(0o644)
        check_pair(key, cert, run)
        for path in (key, cert):
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
        key.replace(private_key)
        cert.replace(certificate)


def write_pointer(root: Path, name: str, identity: str) -> None:
    identity_paths(root, identity)
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False, prefix='.identity-') as stream:
        temporary = Path(stream.name)
        json.dump({'schema': 1, 'identity': identity}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(0o644)
        temporary.replace(root / (name + '.json'))
        descriptor = os.open(root, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def manage_identity(action: str, run, root: Path = ROOT):
    from .operations import CallableRunner, OperationResult, StepResult, _firmware_supports_secure_boot
    result = OperationResult(action)
    if action not in {'prepare-identity', 'regenerate-identity'}:
        raise ValueError('Unsupported identity operation')
    if not _firmware_supports_secure_boot(result, run):
        return result
    # This is a privileged store, not a user-selected output directory.
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    (root / 'identities').mkdir(exist_ok=True, mode=0o755)
    for path in (root, root / 'identities'):
        if path.exists() and (path.is_symlink() or path.stat().st_uid != os.geteuid()
                              or path.stat().st_mode & 0o022):
            raise ValueError('Signing identity storage must be owned and writable only by its administrator')
    runner = CallableRunner(run)
    # Never delete enrolled MOKs. A candidate can coexist with the working key.
    candidate = current_identity(root)
    if action == 'regenerate-identity' or not candidate:
        # A foreign pending request must not be silently replaced by our import.
        pending = runner.run(['mokutil', '--list-new'])
        if action == 'regenerate-identity' and candidate and pending.returncode == 0 and pending.stdout.strip():
            from .inspect import _certificate_fingerprint, _listed_sha1_fingerprints
            fingerprint = _certificate_fingerprint(identity_paths(root, candidate)[1], runner)
            if fingerprint and _listed_sha1_fingerprints(pending.stdout) == {fingerprint}:
                revoked = run(['mokutil', '--revoke-import'], timeout=30)
                if revoked.returncode:
                    raise ValueError('Could not cancel this candidate enrollment request')
                pending = runner.run(['mokutil', '--list-new'])
        if pending.returncode or pending.stdout.strip():
            raise ValueError('A MOK request is already pending or unreadable. Complete it before creating another identity.')
        old = candidate
        candidate = secrets.token_hex(16)
        key, cert = identity_paths(root, candidate)
        legacy = inspect_identity(runner, root)
        if (action == 'prepare-identity' and not old
                and legacy.purpose == 'unified' and legacy.key_present):
            # Re-enroll a suitable existing identity without rotating its key.
            check_pair(LEGACY_KEY, LEGACY_CERTIFICATE, run)
            key.parent.mkdir(parents=True, mode=0o755)
            with tempfile.TemporaryDirectory(dir=key.parent) as temporary:
                staged_key, staged_cert = Path(temporary) / 'key', Path(temporary) / 'cert'
                shutil.copyfile(LEGACY_KEY, staged_key)
                staged_key.chmod(0o600)
                shutil.copyfile(LEGACY_CERTIFICATE, staged_cert)
                staged_cert.chmod(0o644)
                check_pair(staged_key, staged_cert, run)
                staged_key.replace(key)
                staged_cert.replace(cert)
        else:
            generate_identity(key, cert, run)
        if old and legacy.usable:
            # Keep a working identity available while its replacement awaits
            # enrollment. Old files and MOK entries are never removed.
            write_pointer(root, 'previous', old)
        write_pointer(root, 'current', candidate)
        result.steps['key_created'] = StepResult('success')
    else:
        key, cert = identity_paths(root, candidate)
        check_pair(key, cert, run)
        result.steps['key_created'] = StepResult('skipped', 'Existing candidate retained')
    state = inspect_identity(runner, root)
    if state.purpose != 'unified' or state.enrollment == 'unknown':
        raise ValueError('Certificate purpose or enrollment could not be verified; existing boot chain is unchanged.')
    if state.enrollment == 'absent':
        pending = runner.run(['mokutil', '--list-new'])
        if pending.returncode or pending.stdout.strip():
            raise ValueError('Another enrollment request is pending. Complete it first.')
        # Keep the existing application's documented MOKManager code. This is
        # physical-presence enrollment, not the key's encryption password.
        from .operations import ENROLLMENT_PASSWORD
        imported = run(['mokutil', '--import', str(cert)],
                       stdin=f'{ENROLLMENT_PASSWORD}\n{ENROLLMENT_PASSWORD}\n', timeout=120)
        if imported.returncode or enrollment_state(cert, runner) not in {'pending', 'enrolled'}:
            raise ValueError('The enrollment request was not confirmed. Retry with the same candidate certificate.')
    final_enrollment = enrollment_state(cert, runner)
    if final_enrollment not in {'pending', 'enrolled'}:
        raise ValueError('Enrollment state changed or could not be verified. Check again; the old boot chain is unchanged.')
    result.reboot_required = final_enrollment == 'pending'
    if result.reboot_required:
        timeout = run(['mokutil', '--timeout', '-1'], timeout=30)
        result.steps['enrollment_timeout'] = StepResult('success' if timeout.returncode == 0 else 'skipped',
            '' if timeout.returncode == 0 else 'Press a key promptly when MOKManager appears at startup.')
    else:
        from .defaults import sync_defaults
        result.steps.update(sync_defaults(run, root).steps)
    result.steps['enrollment_queued'] = StepResult('success',
        'Old certificates, current bootloader and module signatures are preserved.')
    return result
