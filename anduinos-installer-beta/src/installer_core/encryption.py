"""Small cryptsetup boundary for new, whole-disk encrypted installations.

Policy is serialized in InstallPlan; credentials are not. Human credentials
arrive through a separate pipe and new key material is passed through anonymous
RAM files. Only the random machine credential is persisted, inside encrypted
root, for the boot-tools Dracut handoff. Nothing is written to ESP or recovery.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from .command import CommandRunner
from .storage_graph import EncryptedVolumeType

if TYPE_CHECKING:
    from .steps import InstallContext
    from .storage_commands import EncryptedVolumeCommandPlan


# Bound the memory cost even on large Live machines. cryptsetup calibrates the
# iteration count on the actual CPU; GRUB/input performance remains a later
# boot-chain acceptance gate, not a reason to downgrade human slots to PBKDF2.
HUMAN_PBKDF = (
    "--pbkdf", "argon2id", "--pbkdf-memory", "65536",
    "--pbkdf-parallel", "1", "--iter-time", "1000",
)
MACHINE_PBKDF = ("--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000")
MACHINE_KEY_PATH = Path("etc/anduinos-luks/machine.key")
ROOT_BOOT_MAPPING = "anduinos-root"
SWAP_BOOT_MAPPING = "anduinos-swap"
SWAP_CIPHER = "aes-xts-plain64"
SWAP_KEY_BITS = "512"


@dataclass
class EncryptionCredentials:
    passphrase: str = field(repr=False)
    recovery_key: str = field(repr=False)

    def validate(self) -> None:
        # This backend currently qualifies printable ASCII only, matching the
        # GRUB input POC. The eventual password page must state that limit;
        # accepting Unicode here before its boot input works would lock users out.
        if (not isinstance(self.passphrase, str)
                or not re.fullmatch(r"[ -~]{8,128}", self.passphrase)):
            raise ValueError("Disk password must contain 8 to 128 printable ASCII characters")
        if (not isinstance(self.recovery_key, str)
                or not re.fullmatch(r"[0-9a-f]{48}", self.recovery_key)):
            raise ValueError("A generated 48-character recovery key is required")
        if self.passphrase == self.recovery_key:
            raise ValueError("Disk password and recovery key must differ")

    def clear(self) -> None:
        # Drop retained references; Python cannot promise physical memory erasure.
        self.passphrase = ""
        self.recovery_key = ""


def generate_recovery_key() -> str:
    return secrets.token_hex(24)


def read_encryption_credentials(stream: TextIO) -> EncryptionCredentials:
    """Read one bounded private line, never include its content in an error."""
    try:
        line = stream.readline(4097)
        if not line or len(line) > 4096:
            raise ValueError
        value = json.loads(line)
        if not isinstance(value, dict) or set(value) != {"passphrase", "recovery_key"}:
            raise ValueError
        result = EncryptionCredentials(value["passphrase"], value["recovery_key"])
    except (ValueError, TypeError):
        raise ValueError("Invalid or missing disk encryption credentials") from None
    result.validate()
    return result


@contextmanager
def _key_file(data: bytes):
    """Expose a key to cryptsetup without a pathname on a mounted filesystem."""
    descriptor = os.memfd_create("anduinos-install-key", os.MFD_CLOEXEC)
    try:
        os.fchmod(descriptor, 0o600)
        if os.write(descriptor, data) != len(data):
            raise RuntimeError("Could not prepare disk encryption credentials")
        os.lseek(descriptor, 0, os.SEEK_SET)
        yield descriptor, f"/proc/self/fd/{descriptor}"
    finally:
        os.close(descriptor)


def _add_key(
    runner: CommandRunner,
    source: str,
    passphrase: str,
    new_key: bytes,
    pbkdf: tuple[str, ...],
    slot: int,
) -> None:
    with _key_file(new_key) as (descriptor, key_path):
        runner.run(
            ("cryptsetup", "luksAddKey", "--batch-mode", "--key-file", "-",
             *pbkdf, "--new-key-slot", str(slot), source, key_path),
            input_text=passphrase, pass_fds=(descriptor,),
            log_output=False, timeout=180,
        )
        # Verify before proceeding. A successful add-key exit alone is not
        # evidence that the supplied recovery/machine credential can unlock.
        os.lseek(descriptor, 0, os.SEEK_SET)
        runner.run(
            ("cryptsetup", "open", "--test-passphrase", "--key-slot", str(slot),
             "--key-file", key_path, source),
            pass_fds=(descriptor,), log_output=False, timeout=180,
        )


def preflight_encryption(
    context: InstallContext,
    runner: CommandRunner,
    volumes: tuple[EncryptedVolumeCommandPlan, ...],
) -> None:
    if not volumes:
        if context.encryption_credentials is not None:
            raise ValueError("Disk credentials require an encrypted installation")
        return
    credentials = context.encryption_credentials
    if not isinstance(credentials, EncryptionCredentials):
        raise ValueError("Disk encryption credentials are required before writing the disk")
    credentials.validate()
    runner.require_commands(("cryptsetup", "blkid"))
    for volume in volumes:
        if os.path.lexists(volume.device):
            raise RuntimeError("Installation mapping is already in use: " + volume.device)


def prepare_encrypted_volume(
    context: InstallContext,
    runner: CommandRunner,
    volume: EncryptedVolumeCommandPlan,
) -> None:
    """Create/open immediately before its declared inner filesystem format."""
    if os.path.lexists(volume.device):
        raise RuntimeError("Installation mapping became occupied: " + volume.device)
    if volume.volume_type is EncryptedVolumeType.LUKS2:
        credentials = context.encryption_credentials
        if not isinstance(credentials, EncryptionCredentials):
            raise RuntimeError("Disk encryption credentials are unavailable")
        runner.run(
            ("cryptsetup", "luksFormat", "--batch-mode", "--type", "luks2",
             "--cipher", "aes-xts-plain64", "--key-size", "512",
             *HUMAN_PBKDF, "--key-slot", "0", "--key-file", "-", volume.source),
            input_text=credentials.passphrase, log_output=False, timeout=180,
        )
        _add_key(runner, volume.source, credentials.passphrase,
                 credentials.recovery_key.encode("ascii"), HUMAN_PBKDF, 1)
        runner.run(
            ("cryptsetup", "open", "--type", "luks2", "--key-file", "-",
             volume.source, volume.mapper_name),
            input_text=credentials.passphrase, log_output=False, timeout=180,
        )
    elif volume.volume_type is EncryptedVolumeType.RANDOM_SWAP:
        # No LUKS header or persistent key: systemd creates a new swap cipher
        # and runs mkswap at every boot. This deliberately excludes hibernation.
        runner.run(
            ("cryptsetup", "open", "--batch-mode", "--type", "plain", "--cipher", SWAP_CIPHER,
             "--key-size", SWAP_KEY_BITS, "--key-file", "/dev/urandom",
             volume.source, volume.mapper_name),
            # A previous layout can leave a recognizable signature at this
            # location. The declared inner FORMAT is destructive already;
            # plain-open must not wait for a second, invisible TTY confirmation.
            input_text="", log_output=False, timeout=30,
        )
    else:
        raise ValueError("Unsupported encrypted volume type")
    context.values.setdefault("opened_encrypted_volumes", []).append(volume)


def install_machine_key(
    context: InstallContext, runner: CommandRunner, target: Path,
) -> None:
    """Provision the random slot only after the encrypted target is mounted."""
    credentials = context.encryption_credentials
    if not isinstance(credentials, EncryptionCredentials):
        raise RuntimeError("Disk encryption credentials are unavailable")
    volume = next(item for item in context.values["opened_encrypted_volumes"]
                  if item.volume_type is EncryptedVolumeType.LUKS2)
    directory = target / MACHINE_KEY_PATH.parent
    if directory.is_symlink():
        raise RuntimeError("The target encryption directory must not be a symbolic link")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    key = secrets.token_bytes(64)
    # Refuse inherited identity. CopySystem excludes this directory, so an
    # existing file means this installation step is being replayed incorrectly.
    descriptor = os.open(target / MACHINE_KEY_PATH,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(key)
            output.flush()
            os.fsync(output.fileno())
        _add_key(runner, volume.source, credentials.passphrase, key, MACHINE_PBKDF, 2)
        # LUKS2 owns credential roles, so rolling root back cannot resurrect an
        # obsolete password policy. No secret is stored in this token.
        token = dict(type="anduinos-credentials", version=1,
                     keyslots=["0", "1", "2"],
                     roles=dict(password="0", recovery="1", machine="2"), pending=None)
        runner.run(
            ("cryptsetup", "token", "import", "--token-id", "0", "--json-file", "-", volume.source),
            input_text=json.dumps(token), log_output=False, timeout=30,
        )
        metadata = runner.run(
            ("cryptsetup", "luksDump", "--dump-json-metadata", volume.source),
            log_output=False, timeout=30,
        )
        if json.loads(metadata.stdout).get("tokens", {}).get("0") != token:
            raise RuntimeError("Could not verify disk credential roles")
    except Exception:
        (target / MACHINE_KEY_PATH).unlink()
        raise
    credentials.clear()
    context.encryption_credentials = None


def close_encrypted_volumes(
    context: InstallContext, runner: CommandRunner, *, strict: bool,
) -> None:
    """Only successful opens from this context are ours to close."""
    for volume in reversed(tuple(context.values.get("opened_encrypted_volumes", []))):
        result = runner.run(("cryptsetup", "close", volume.mapper_name),
                            check=False, timeout=30)
        if result.returncode == 0:
            context.values["opened_encrypted_volumes"].remove(volume)
        elif strict:
            raise RuntimeError("Could not close installation mapping: " + volume.mapper_name)
        else:
            context.log("Installation mapping could not be closed: " + volume.mapper_name)
