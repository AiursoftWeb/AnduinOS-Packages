"""Detection of the AnduinOS Dracut Live environment."""

from __future__ import annotations

from pathlib import Path


LIVE_MARKERS = (
    Path("/run/anduinos-live/rootfs.squashfs"),
    Path("/run/anduinos-live/environment"),
)


def is_trusted_live_environment(
    *,
    environment: Path = Path("/run/anduinos-live/environment"),
    source: Path = Path("/run/anduinos-live/rootfs.squashfs"),
    media: Path = Path("/cdrom"),
) -> bool:
    """Require the actual Dracut Live runtime before exposing a root shell."""
    try:
        return (
            environment.is_file()
            and "ANDUINOS_LIVE=1" in environment.read_text(encoding="utf-8").splitlines()
            and source.is_file()
            and source.stat().st_size > 0
            and media.is_dir()
        )
    except (OSError, UnicodeError):
        return False


def is_live_environment(
    *,
    markers: tuple[Path, ...] = LIVE_MARKERS,
    cmdline_path: Path = Path("/proc/cmdline"),
) -> bool:
    """Return true only when an explicit Live-runtime marker is present."""

    if any(path.exists() for path in markers):
        return True
    try:
        words = set(cmdline_path.read_text(encoding="utf-8").split())
    except OSError:
        return False
    return bool(
        words.intersection({"rd.anduinos.live=1", "rd.live.image", "boot=live"})
    )
