"""Detection of the AnduinOS Dracut Live environment."""

from __future__ import annotations

from pathlib import Path


LIVE_MARKERS = (
    Path("/run/anduinos-live/rootfs.squashfs"),
    Path("/run/anduinos-live/environment"),
)


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
