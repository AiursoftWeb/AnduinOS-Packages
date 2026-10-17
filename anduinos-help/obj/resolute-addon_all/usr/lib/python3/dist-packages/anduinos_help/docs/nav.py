"""Parser for the AnduinOS-Docs ``properdocs.yml`` navigation config.

The AnduinOS-Docs repository (https://github.com/AiursoftWeb/AnduinOS-Docs)
ships a ``properdocs.yml`` at its root that follows MkDocs conventions:

.. code-block:: yaml

    site_name: AnduinOS Documentation
    docs_dir: Docs
    nav:
      - Home:
        - Documentation: README.md
        - Release Notes:
          - Overview: CHANGELOG.md
          - AnduinOS 2.0.3: Release-Notes/2.0.3.md
      - Install:
        - Installing AnduinOS:
          - System Requirements: Install/System-Requirements.md
          ...

This module parses that YAML into a flat list of ordered nav entries
(with parent/child relationships preserved) that the SQLite store can
persist.

We use PyYAML (already a runtime dependency for the sync flow) rather
than a hand-rolled parser — MkDocs YAML is forgiving and we want to
stay compatible with whatever the docs authors throw at us.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anduinos_help.utils.logging import get_logger

_log = get_logger("docs.nav")


@dataclass
class NavNode:
    """A node in the parsed nav tree."""
    title: str
    path: str | None = None       # repo-relative .md path OR external URL
    is_external: bool = False
    is_group: bool = False
    children: list["NavNode"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "path": self.path,
            "is_external": self.is_external,
            "is_group": self.is_group,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class NavConfig:
    """The parsed ``properdocs.yml``."""
    docs_dir: str = "Docs"
    nav: list[NavNode] = field(default_factory=list)
    site_name: str = ""
    repo_url: str = ""
    edit_uri: str = ""


def parse_properdocs_yml(repo_root: Path) -> NavConfig | None:
    """Parse the ``properdocs.yml`` at the root of ``repo_root``.

    Returns ``None`` if the file doesn't exist or can't be parsed (in
    which case the caller should fall back to an alphabetical listing).
    """
    yml_path = repo_root / "properdocs.yml"
    if not yml_path.is_file():
        _log.info("properdocs.yml not found at %s — falling back to alphabetical nav", yml_path)
        return None

    try:
        import yaml  # PyYAML
    except ImportError:
        _log.error("PyYAML not available — cannot parse properdocs.yml")
        return None

    try:
        data = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _log.error("Failed to parse properdocs.yml: %s", exc)
        return None

    if not isinstance(data, dict):
        _log.error("properdocs.yml top-level is not a mapping")
        return None

    config = NavConfig(
        docs_dir=str(data.get("docs_dir") or "Docs").strip("/"),
        site_name=str(data.get("site_name") or ""),
        repo_url=str(data.get("repo_url") or ""),
        edit_uri=str(data.get("edit_uri") or ""),
    )

    raw_nav = data.get("nav")
    if not isinstance(raw_nav, list):
        _log.warning("properdocs.yml has no 'nav' list — falling back to alphabetical")
        return config

    config.nav = _parse_nav_list(raw_nav)
    return config


def _parse_nav_list(entries: list[Any]) -> list[NavNode]:
    """Parse a YAML nav list into :class:`NavNode` objects."""
    out: list[NavNode] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Each entry is a single-key dict: { title: value }
        for key, value in entry.items():
            title = str(key)
            if isinstance(value, str):
                # Leaf node — internal .md path or external URL
                is_ext = value.startswith(("http://", "https://", "mailto:"))
                out.append(NavNode(
                    title=title,
                    path=value,
                    is_external=is_ext,
                    is_group=False,
                ))
            elif isinstance(value, list):
                # Group node with children
                out.append(NavNode(
                    title=title,
                    path=None,
                    is_external=False,
                    is_group=True,
                    children=_parse_nav_list(value),
                ))
            # ignore other types silently
    return out


def walk(node: NavNode):
    """Yield ``node`` and all its descendants, depth-first."""
    yield node
    for child in node.children:
        yield from walk(child)


def all_paths(nodes: list[NavNode]) -> set[str]:
    """Return the set of internal (non-external) paths referenced by ``nodes``."""
    paths: set[str] = set()
    for n in nodes:
        for descendant in walk(n):
            if descendant.path and not descendant.is_external:
                # Normalise: drop leading "./", strip slashes
                p = descendant.path.lstrip("./").lstrip("/")
                paths.add(p)
    return paths


def title_for_path(nodes: list[NavNode], file_path: str) -> str | None:
    """Find the human-readable title for ``file_path`` in the nav tree.

    Returns ``None`` if the path isn't listed in ``properdocs.yml``.
    """
    for n in nodes:
        for descendant in walk(n):
            if descendant.path and not descendant.is_external:
                p = descendant.path.lstrip("./").lstrip("/")
                if p == file_path:
                    return descendant.title
    return None
