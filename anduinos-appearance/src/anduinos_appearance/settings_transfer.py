"""Export and restore the user's GNOME dconf settings."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


GNOME_DCONF_PATH = "/org/gnome/"
SAFETY_BACKUP_NAME = "gnome-settings-before-import.ini"


class InvalidSettingsFile(ValueError):
    """Raised when a selected file is not a non-empty dconf keyfile."""


class SettingsImportError(RuntimeError):
    """Raised when dconf could not import the selected settings."""

    def __init__(self, message: str, *, rollback_succeeded: bool):
        super().__init__(message)
        self.rollback_succeeded = rollback_succeeded


def dump_gnome_settings() -> bytes:
    """Return an unmodified dconf dump of /org/gnome/."""
    result = subprocess.run(
        ["dconf", "dump", GNOME_DCONF_PATH],
        check=True,
        capture_output=True,
    )
    return result.stdout


def validate_settings_file(contents: bytes) -> None:
    """Reject empty files and files that do not resemble dconf keyfiles."""
    meaningful_lines = [
        line.strip()
        for line in contents.splitlines()
        if line.strip() and not line.lstrip().startswith((b"#", b";"))
    ]
    if not meaningful_lines or not any(
        line.startswith(b"[") and line.endswith(b"]")
        for line in meaningful_lines
    ):
        raise InvalidSettingsFile("The file is not a non-empty dconf keyfile")


def safety_backup_path() -> Path:
    """Return the private per-user path used for the latest import backup."""
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        base = Path(state_home)
    else:
        base = Path.home() / ".local" / "state"
    return base / "anduinos-appearance" / SAFETY_BACKUP_NAME


def _write_private_backup(contents: bytes, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _load_gnome_settings(contents: bytes) -> None:
    subprocess.run(
        ["dconf", "load", GNOME_DCONF_PATH],
        input=contents,
        check=True,
        capture_output=True,
    )


def _reset_gnome_settings() -> None:
    subprocess.run(
        ["dconf", "reset", "-f", GNOME_DCONF_PATH],
        check=True,
        capture_output=True,
    )


def import_gnome_settings(
    contents: bytes,
    *,
    strict: bool,
    backup_path: Path | None = None,
) -> Path:
    """Import a dconf keyfile, preserving a snapshot and rolling back failures.

    Merge mode only loads keys present in ``contents``. Strict mode resets the
    entire GNOME subtree before loading it. Both modes save the previous state
    to a private INI file before making any changes.
    """
    validate_settings_file(contents)
    previous_settings = dump_gnome_settings()
    destination = backup_path or safety_backup_path()
    _write_private_backup(previous_settings, destination)

    try:
        if strict:
            _reset_gnome_settings()
        _load_gnome_settings(contents)
    except (OSError, subprocess.CalledProcessError) as import_error:
        try:
            _reset_gnome_settings()
            _load_gnome_settings(previous_settings)
        except (OSError, subprocess.CalledProcessError) as rollback_error:
            raise SettingsImportError(
                f"Import failed and the previous settings could not be restored: "
                f"{rollback_error}",
                rollback_succeeded=False,
            ) from import_error
        raise SettingsImportError(
            "Import failed; the previous settings were restored",
            rollback_succeeded=True,
        ) from import_error

    return destination
