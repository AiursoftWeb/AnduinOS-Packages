"""Stable destructive-boundary markers for disposable VM campaigns."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .steps import InstallContext


BOUNDARY_ID_RE = re.compile(r"^guided-[a-z0-9]+(?:-[a-z0-9]+)*$")
BOUNDARY_PHASES = {"before", "after"}


def boundary_marker(boundary_id: str, phase: str) -> str:
    if not BOUNDARY_ID_RE.fullmatch(boundary_id):
        raise ValueError("Invalid destructive boundary identifier")
    if phase not in BOUNDARY_PHASES:
        raise ValueError("Invalid destructive boundary phase")
    return f"[anduinos-boundary:{boundary_id}:{phase}]"


def emit_boundary(
    context: "InstallContext",
    boundary_id: str,
    phase: str,
) -> None:
    context.log(boundary_marker(boundary_id, phase))
