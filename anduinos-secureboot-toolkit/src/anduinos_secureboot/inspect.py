"""Read-only inspection of the Secure Boot and DKMS trust chain."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Protocol, Sequence

from .model import (
    DkmsState,
    ModuleState,
    SecureBootState,
    SecureBootStatus,
)


MOK_PRIVATE_KEY = Path("/var/lib/shim-signed/mok/MOK.priv")
MOK_CERTIFICATE = Path("/var/lib/shim-signed/mok/MOK.der")
DKMS_CONFIG = Path("/etc/dkms/framework.conf.d/anduinos-sb-sign.conf")


class Runner(Protocol):
    def run(
        self, command: Sequence[str], timeout: int = 10
    ) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    def run(
        self, command: Sequence[str], timeout: int = 10
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["LC_ALL"] = "C"
        environment["LANG"] = "C"
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            return subprocess.CompletedProcess(command, 127, "", str(error))


def normalize_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9a-f]", "", value.lower())
    return normalized or None


def _command_available(command: str, runner: Runner) -> bool:
    if isinstance(runner, SubprocessRunner):
        return shutil.which(command) is not None
    result = runner.run([command, "--version"])
    return result.returncode == 0


def _certificate_serial(certificate: Path, runner: Runner) -> str | None:
    result = runner.run(
        [
            "openssl",
            "x509",
            "-in",
            str(certificate),
            "-inform",
            "DER",
            "-noout",
            "-serial",
        ]
    )
    if result.returncode or "=" not in result.stdout:
        return None
    return normalize_key(result.stdout.strip().split("=", 1)[1])


def _certificate_fingerprint(certificate: Path, runner: Runner) -> str | None:
    result = runner.run(
        [
            "openssl",
            "x509",
            "-in",
            str(certificate),
            "-inform",
            "DER",
            "-noout",
            "-fingerprint",
            "-sha1",
        ]
    )
    if result.returncode or "=" not in result.stdout:
        return None
    return normalize_key(result.stdout.strip().split("=", 1)[1])


def _listed_sha1_fingerprints(output: str) -> frozenset[str]:
    fingerprints = set()
    for value in re.findall(
        r"^SHA1 Fingerprint:\s*([0-9a-f:]+)\s*$", output, re.IGNORECASE | re.MULTILINE
    ):
        normalized = normalize_key(value)
        if normalized:
            fingerprints.add(normalized)
    return frozenset(fingerprints)


def certificate_enrolled(certificate: Path, runner: Runner) -> bool:
    """Return whether the exact certificate is present in a firmware trust DB."""
    fingerprint = _certificate_fingerprint(certificate, runner)
    listed = runner.run(["mokutil", "--list-enrolled"])
    listed_fingerprints = _listed_sha1_fingerprints(listed.stdout)
    if listed.returncode == 0 and fingerprint and (
        listed_fingerprints or not listed.stdout.strip()
    ):
        return fingerprint in listed_fingerprints

    # Upstream mokutil 0.7.2 deliberately returns 0 for "not enrolled" and 1
    # for "already enrolled". Some distributions patch that convention, so a
    # process status can never be treated as an enrollment boolean.
    tested = runner.run(["mokutil", "--test-key", str(certificate)])
    message = f"{tested.stdout}\n{tested.stderr}".lower()
    if "is already enrolled" in message:
        return True
    if "is not enrolled" in message or "isn't enrolled" in message:
        return False
    return False


def certificate_pending(certificate: Path, runner: Runner) -> bool:
    """Return whether the exact certificate is queued for MOK enrollment."""
    fingerprint = _certificate_fingerprint(certificate, runner)
    listed = runner.run(["mokutil", "--list-new"])
    listed_fingerprints = _listed_sha1_fingerprints(listed.stdout)
    return bool(
        listed.returncode == 0
        and fingerprint
        and listed_fingerprints
        and fingerprint in listed_fingerprints
    )


def parse_secure_boot_status(
    result: subprocess.CompletedProcess[str],
) -> SecureBootStatus:
    """Parse every explicit mokutil state without guessing on failures."""
    if result.returncode != 0:
        return SecureBootStatus.UNKNOWN
    output = f"{result.stdout}\n{result.stderr}".lower()
    reported_states = {
        status
        for status, reported in (
            (
                SecureBootStatus.ENABLED,
                "secureboot enabled" in output
                or "secure boot enabled" in output,
            ),
            (
                SecureBootStatus.DISABLED,
                "secureboot disabled" in output
                or "secure boot disabled" in output,
            ),
            (
                SecureBootStatus.UNSUPPORTED,
                "doesn't support secure boot" in output
                or "does not support secure boot" in output,
            ),
        )
        if reported
    }
    if len(reported_states) == 1:
        return reported_states.pop()
    return SecureBootStatus.UNKNOWN


def inspect_secure_boot(
    runner: Runner | None = None,
    private_key: Path = MOK_PRIVATE_KEY,
    certificate: Path = MOK_CERTIFICATE,
    kernel_release: str | None = None,
    configuration: Path = DKMS_CONFIG,
) -> SecureBootState:
    runner = runner or SubprocessRunner()
    state = runner.run(["mokutil", "--sb-state"])
    status = parse_secure_boot_status(state)
    enabled = status is SecureBootStatus.ENABLED
    key_present = private_key.is_file()
    certificate_present = certificate.is_file()
    enrolled = False
    pending = False
    serial = None

    if certificate_present and status not in {
        SecureBootStatus.UNSUPPORTED,
        SecureBootStatus.UNKNOWN,
    }:
        enrolled = certificate_enrolled(certificate, runner)
        serial = _certificate_serial(certificate, runner)
        pending = not enrolled and certificate_pending(certificate, runner)

    release = kernel_release or os.uname().release
    headers_available = Path("/lib/modules", release, "build").exists()
    return SecureBootState(
        enabled=enabled,
        key_present=key_present,
        certificate_present=certificate_present,
        enrolled=enrolled,
        certificate_serial=serial,
        enrollment_pending=pending,
        dkms_available=_command_available("dkms", runner),
        headers_available=headers_available,
        configuration_present=configuration.is_file(),
        status=status,
    )


def module_signature(module: str, runner: Runner) -> str | None:
    result = runner.run(["modinfo", "-F", "sig_key", module])
    if result.returncode == 0:
        return normalize_key(result.stdout)
    # Noble's modinfo and test doubles may only expose the full record.
    result = runner.run(["modinfo", module])
    if result.returncode:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("sig_key:"):
            return normalize_key(line.split(":", 1)[1])
    return None


def inspect_dkms(
    secure_boot: SecureBootState,
    runner: Runner | None = None,
    module_directory: Path | None = None,
) -> DkmsState:
    runner = runner or SubprocessRunner()
    module_directory = module_directory or Path(
        "/lib/modules", os.uname().release, "updates/dkms"
    )
    if not module_directory.is_dir():
        return DkmsState()

    details: list[ModuleState] = []
    try:
        paths = sorted(module_directory.iterdir())
    except OSError:
        return DkmsState()
    for path in paths:
        if not path.name.endswith((".ko", ".ko.xz", ".ko.zst")):
            continue
        signature = module_signature(str(path), runner)
        trusted = bool(
            secure_boot.enforcement_inactive
            or (
                secure_boot.status is SecureBootStatus.ENABLED
                and secure_boot.enrolled
                and signature
                and secure_boot.certificate_serial
                and signature == secure_boot.certificate_serial
            )
        )
        details.append(ModuleState(path.name, str(path), signature, trusted))

    return DkmsState(
        modules=tuple(item.name for item in details),
        trusted_modules=tuple(item.name for item in details if item.trusted),
        untrusted_modules=tuple(item.name for item in details if not item.trusted),
        details=tuple(details),
    )
