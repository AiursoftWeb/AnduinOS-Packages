"""Read-only firmware evidence, shared by desktop clients and root preflight.

mokutil 0.7.2 conflates ENOENT, EACCES and EIO into the same message/exit 255.
That message alone is never proof that Secure Boot is unsupported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import errno
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from .model import SecureBootStatus

EFI = Path("/sys/firmware/efi")
MOUNTINFO = Path("/proc/self/mountinfo")
EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
LIVE_IMAGE = Path("/run/anduinos-live/rootfs.squashfs")


@dataclass(frozen=True)
class FirmwareEvidence:
    uefi: bool | None
    status: SecureBootStatus
    reason: str
    detail: str = ""
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def known(self) -> bool:
        return self.uefi is not None and self.status is not SecureBootStatus.UNKNOWN

    def to_json(self) -> str:
        return json.dumps({"schema": 1, **asdict(self)}, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> FirmwareEvidence:
        data = json.loads(text)
        if not isinstance(data, dict) or data.pop("schema", None) != 1:
            raise ValueError("Invalid firmware evidence schema")
        if type(data.get("uefi")) not in (bool, type(None)):
            raise ValueError("Invalid firmware type")
        for name in ("reason", "detail", "stdout", "stderr"):
            if not isinstance(data.get(name), str):
                raise ValueError("Invalid firmware diagnostic")
        if type(data.get("returncode")) not in (int, type(None)):
            raise ValueError("Invalid firmware return code")
        data["status"] = SecureBootStatus(data["status"])
        result = cls(**data)
        if (result.uefi is None and result.status is not SecureBootStatus.UNKNOWN) or (
            result.uefi is False and result.status is not SecureBootStatus.UNSUPPORTED
        ):
            raise ValueError("Contradictory firmware evidence")
        return result


def reported_states(output: str) -> set[SecureBootStatus]:
    lines = {line.strip().lower() for line in output.splitlines()}
    return {
        state for state, messages in (
            (SecureBootStatus.ENABLED, {"secureboot enabled", "secure boot enabled"}),
            (SecureBootStatus.DISABLED, {"secureboot disabled", "secure boot disabled"}),
            (SecureBootStatus.UNSUPPORTED, {
                "this system doesn't support secure boot",
                "this system does not support secure boot",
            }),
        ) if lines & messages
    }


def parse_secure_boot_status(result: subprocess.CompletedProcess[str]) -> SecureBootStatus:
    """A text parser cannot establish unsupported status on a failed read."""
    states = reported_states(f"{result.stdout}\n{result.stderr}")
    if result.returncode == 0 and len(states) == 1:
        return states.pop()
    return SecureBootStatus.UNKNOWN


def _mount_type(path: Path, mountinfo: Path) -> str | None:
    for line in mountinfo.read_text().splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        if not separator or len(fields) < 5 or not right.split():
            continue
        mountpoint = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
        if mountpoint == str(path):
            return right.split()[0]
    return None


def _read_flag(path: Path) -> int:
    data = path.read_bytes()
    # efivarfs exposes four attribute bytes followed by a UINT8 value.
    if len(data) != 5 or data[4] not in (0, 1):
        raise ValueError(f"Invalid EFI boolean: {path.name} (size={len(data)})")
    return data[4]


def probe_firmware(*, run=subprocess.run, efi_path: Path = EFI,
                   mountinfo: Path = MOUNTINFO) -> FirmwareEvidence:
    try:
        mode = efi_path.stat().st_mode
    except FileNotFoundError:
        return FirmwareEvidence(False, SecureBootStatus.UNSUPPORTED, "legacy-bios")
    except OSError as error:
        return FirmwareEvidence(None, SecureBootStatus.UNKNOWN, "efi-access", str(error))
    if not stat.S_ISDIR(mode):
        return FirmwareEvidence(None, SecureBootStatus.UNKNOWN, "efi-access", "EFI path is not a directory")

    evidence = FirmwareEvidence(True, SecureBootStatus.UNKNOWN, "probe-failed")
    try:
        result = run(["mokutil", "--sb-state"], capture_output=True, text=True,
                     timeout=10, check=False, env=dict(os.environ, LC_ALL="C", LANG="C"))
        evidence = replace(evidence, returncode=result.returncode,
                           stdout=result.stdout or "", stderr=result.stderr or "")
        states = reported_states(f"{evidence.stdout}\n{evidence.stderr}")
        if len(states) > 1:
            return replace(evidence, reason="contradictory-output",
                           detail="mokutil reported contradictory Secure Boot states")
        parsed = parse_secure_boot_status(result)
        if parsed in {SecureBootStatus.ENABLED, SecureBootStatus.DISABLED}:
            return replace(evidence, status=parsed, reason="mokutil")
    except (OSError, subprocess.TimeoutExpired) as error:
        evidence = replace(evidence, detail=str(error))

    variables = efi_path / "efivars"
    try:
        filesystem = _mount_type(variables, mountinfo)
        if filesystem is None:
            return replace(evidence, reason="efivarfs-unmounted",
                           detail="EFI variable filesystem is not mounted")
        if filesystem != "efivarfs":
            return replace(evidence, reason="wrong-filesystem",
                           detail=f"Expected efivarfs, found {filesystem}")
        # Enumerate before reading to distinguish a genuinely absent variable
        # from a registered variable whose firmware GetVariable call fails.
        names = {item.name for item in variables.iterdir()}
        secure_name = f"SecureBoot-{EFI_GLOBAL}"
        try:
            secure = _read_flag(variables / secure_name)
        except FileNotFoundError:
            if secure_name in names or _mount_type(variables, mountinfo) != "efivarfs":
                raise OSError(errno.EIO, "EFI variable disappeared while reading")
            # Re-enumerate: a disappearing mount/directory is not unsupported.
            if secure_name in {item.name for item in variables.iterdir()}:
                raise OSError(errno.EIO, "EFI variable changed while reading")
            return replace(evidence, status=SecureBootStatus.UNSUPPORTED,
                           reason="secureboot-variable-absent", detail="")
        setup = _read_flag(variables / f"SetupMode-{EFI_GLOBAL}")
        if secure == 1 and setup == 1:
            raise ValueError("SecureBoot is enabled while SetupMode is active")
        status = SecureBootStatus.ENABLED if secure == 1 else SecureBootStatus.DISABLED
        return replace(evidence, status=status, reason="efi-variables", detail="")
    except PermissionError as error:
        return replace(evidence, reason="permission-denied", detail=str(error))
    except (OSError, ValueError) as error:
        return replace(evidence, reason="efi-read-failed", detail=str(error))


def prepare_live_interface(*, run=subprocess.run, efi_path: Path = EFI,
                           mountinfo: Path = MOUNTINFO,
                           live_image: Path = LIVE_IMAGE) -> None:
    """Prepare only the fixed Live firmware interface; never write variables."""
    if os.geteuid() != 0:
        raise PermissionError("Firmware preparation requires root")
    if not live_image.is_file() or not efi_path.is_dir():
        return
    variables = efi_path / "efivars"
    if _mount_type(variables, mountinfo) is not None:
        return
    if variables.is_symlink():
        raise ValueError("Refusing a redirected EFI variable mountpoint")
    variables.mkdir(exist_ok=True)
    run(["/usr/bin/mount", "-t", "efivarfs", "-o", "nosuid,nodev,noexec",
         "efivarfs", str(variables)], check=True, capture_output=True, text=True, timeout=10)


def privileged_probe() -> FirmwareEvidence:
    """Fixed root helper operation; no caller-selected paths or commands."""
    if os.geteuid() != 0:
        raise PermissionError("Firmware inspection requires root")
    try:
        prepare_live_interface()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return FirmwareEvidence(True, SecureBootStatus.UNKNOWN, "efi-preparation-failed", str(error))
    return probe_firmware()


def recover_firmware(evidence: FirmwareEvidence, *, run=subprocess.run) -> FirmwareEvidence:
    """Recheck access failures through a dedicated Polkit entry point."""
    if evidence.known or evidence.reason not in {"permission-denied", "efivarfs-unmounted", "efi-access"}:
        return evidence
    if os.geteuid() == 0:
        return privileged_probe()
    try:
        result = run(["pkexec", "/usr/libexec/anduinos-firmware-probe"],
                     capture_output=True, text=True, timeout=120, check=False)
        if result.returncode != 0:
            return replace(evidence, reason="helper-failed",
                           detail=f"Firmware helper exited {result.returncode}: {result.stderr.strip()}")
        return FirmwareEvidence.from_json(result.stdout)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        return replace(evidence, reason="helper-failed", detail=str(error))
