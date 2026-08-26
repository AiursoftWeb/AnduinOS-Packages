"""Small, side-effect-free system probes used by the Control Panel UI."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import shutil
import subprocess


Runner = Callable[..., subprocess.CompletedProcess[str]]

BOTTLES_APP_ID = "com.usebottles.bottles"
DEJA_DUP_APP_ID = "org.gnome.DejaDup"
SNAPSHOT_PACKAGE = "anduinos-btrfs-snapshots-manager"
WHY_AI_PACKAGE = "anduinos-why-ai"
WHY_PLACEHOLDER_PACKAGE = "anduinos-why-placeholder"


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
