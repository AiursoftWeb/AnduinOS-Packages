"""Small, side-effect-free system probes used by the Control Panel UI."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess


Runner = Callable[..., subprocess.CompletedProcess[str]]

BOTTLES_APP_ID = "com.usebottles.bottles"
DEJA_DUP_APP_ID = "org.gnome.DejaDup"
SNAPSHOT_PACKAGE = "anduinos-btrfs-snapshots-manager"
VOICE_TYPING_PACKAGE = "anduinos-whisper-gtk"
WHY_AI_PACKAGE = "anduinos-why-ai"
WHY_PLACEHOLDER_PACKAGE = "anduinos-why-placeholder"
DEFAULT_GRUB_TIMEOUT = 10
DEFAULT_GRUB_RECORDFAIL_TIMEOUT = 30
GRUB_DISPLAY_NATIVE = "native"
GRUB_DISPLAY_LARGE_TEXT = "large-text"
DEFAULT_GRUB_DISPLAY_MODE = GRUB_DISPLAY_LARGE_TEXT
GRUB_DEFAULTS = Path("/etc/default/grub")
GRUB_DEFAULTS_DIRECTORY = Path("/etc/default/grub.d")
GRUB_TIMEOUT_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?P<name>GRUB_(?:RECORDFAIL_)?TIMEOUT)\s*=\s*"
    r"(?P<quote>['\"]?)(?P<value>[0-9]+)(?P=quote)\s*(?:#.*)?$"
)
GRUB_GFXMODE_PATTERN = re.compile(
    r"^\s*(?:export\s+)?GRUB_GFXMODE\s*=\s*"
    r"(?P<quote>['\"]?)(?P<value>[^'\"\s#]+)(?P=quote)\s*(?:#.*)?$"
)


@dataclass(frozen=True)
class GrubTimeouts:
    """Effective GRUB menu delays exposed by the boot settings UI."""

    normal: int
    after_interrupted_boot: int


def _run(arguments: Sequence[str], runner: Runner = subprocess.run) -> subprocess.CompletedProcess[str]:
    return runner(
        list(arguments),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def command_available(command: str) -> bool:
    """Return whether *command* can be launched from PATH."""

    return shutil.which(command) is not None


def package_installed(package: str, runner: Runner = subprocess.run) -> bool:
    """Return whether dpkg considers *package* fully installed."""

    result = _run(
        ["dpkg-query", "--show", "--showformat=${db:Status-Abbrev}", package],
        runner,
    )
    return result.returncode == 0 and result.stdout.strip().startswith("ii")


def flatpak_installed(app_id: str, runner: Runner = subprocess.run) -> bool:
    """Check both the per-user and system Flatpak installations."""

    for scope in ("--user", "--system"):
        if _run(["flatpak", scope, "info", app_id], runner).returncode == 0:
            return True
    return False


def read_grub_timeouts(
    defaults: Path = GRUB_DEFAULTS,
    defaults_directory: Path = GRUB_DEFAULTS_DIRECTORY,
) -> GrubTimeouts:
    """Read literal timeout assignments without executing shell configuration."""

    values = {
        "GRUB_TIMEOUT": DEFAULT_GRUB_TIMEOUT,
        "GRUB_RECORDFAIL_TIMEOUT": DEFAULT_GRUB_RECORDFAIL_TIMEOUT,
    }
    paths = [defaults]
    try:
        paths.extend(sorted(defaults_directory.glob("*.cfg")))
    except OSError:
        pass

    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            match = GRUB_TIMEOUT_PATTERN.fullmatch(line)
            if match:
                values[match.group("name")] = int(match.group("value"))

    return GrubTimeouts(
        normal=values["GRUB_TIMEOUT"],
        after_interrupted_boot=values["GRUB_RECORDFAIL_TIMEOUT"],
    )


def read_grub_display_mode(
    defaults: Path = GRUB_DEFAULTS,
    defaults_directory: Path = GRUB_DEFAULTS_DIRECTORY,
) -> str:
    """Map the effective literal GRUB graphics mode to a UI choice."""

    gfxmode = ""
    paths = [defaults]
    try:
        paths.extend(sorted(defaults_directory.glob("*.cfg")))
    except OSError:
        pass

    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            match = GRUB_GFXMODE_PATTERN.fullmatch(line)
            if match:
                gfxmode = match.group("value")

    return (
        GRUB_DISPLAY_NATIVE
        if gfxmode.casefold() == "auto"
        else DEFAULT_GRUB_DISPLAY_MODE
    )
