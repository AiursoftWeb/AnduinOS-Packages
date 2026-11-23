"""Background documentation sync job.

Inspired by Aiursoft.DocsViewer's ``SyncDocsRepoJob`` +
``IndexDocumentsJob``: a background job that

1. Clones (or pulls) the AnduinOS-Docs git repository into
   ``XDG_DATA_HOME/anduinos-help/docs-repo/``.
2. Parses ``properdocs.yml`` to get the canonical nav tree + per-doc
   titles.
3. Walks the ``Docs/`` directory, reads each ``.md`` file, and upserts
   it into the SQLite store (replacing the old flat-file catalog).
4. Soft-deletes documents whose files have disappeared.
5. Persists the nav tree so the sidebar can render it without
   re-parsing YAML.

The job is designed to run **on app startup** (so the user sees
up-to-date docs as soon as possible) and on demand via the
``win.refresh-docs`` action. It is safe to run repeatedly — every step
is idempotent.

Network behaviour: the job uses ``git`` (subprocess) to clone/pull,
which respects the user's network proxy and SSH key configuration.
If the network is unreachable, the job logs a warning and exits
silently — the app continues to work with whatever is in the SQLite
store from the last successful sync.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from anduinos_help.docs import nav, store
from anduinos_help.utils.logging import get_logger
from anduinos_help.utils.paths import (
    docs_repo_root,
    docs_markdown_dir,
    last_sync_path,
    sync_lock_path,
)

_log = get_logger("docs.sync")

# Source of truth — the AnduinOS docs git repository.
DOCS_REPO_URL = "https://github.com/AiursoftWeb/AnduinOS-Docs.git"
DOCS_REPO_BLOB = "https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/"
DOCS_SITE = "https://docs.anduinos.com/"

# Soft lock so we don't run two syncs at once.
_sync_lock = threading.Lock()


@dataclass
class SyncResult:
    """Outcome of a single sync run."""
    success: bool
    documents_indexed: int = 0
    documents_deleted: int = 0
    error: str | None = None
    duration_seconds: float = 0.0


def is_syncing() -> bool:
    """Return True if a sync is currently in progress."""
    return _sync_lock.locked()


def sync_blocking(force: bool = False) -> SyncResult:
    """Run a full sync. **Blocking** — call from a worker thread.

    Parameters
    ----------
    force
        If True, re-clone even if the repo already exists. Useful for
        recovering from a corrupted local clone.

    Returns
    -------
    SyncResult
        Summary of what happened.
    """
    if not _sync_lock.acquire(blocking=False):
        _log.info("sync_blocking: another sync is already running — skipping")
        return SyncResult(success=False, error="already-running")
    started = datetime.now(timezone.utc)
    try:
        return _sync_inner(force=force, started=started)
    finally:
        _sync_lock.release()


def _sync_inner(force: bool, started: datetime) -> SyncResult:
    # 1. Make sure the database exists.
    store.init_db()

    # 2. Clone or pull the repo.
    try:
        _ensure_repo_cloned(force=force)
    except Exception as exc:  # noqa: BLE001
        _log.error("Sync failed during repo clone/pull: %s", exc)
        return SyncResult(
            success=False,
            error=f"repo-clone-failed: {exc}",
            duration_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
        )

    repo_root = docs_repo_root()
    if not repo_root.is_dir():
        return SyncResult(
            success=False,
            error="repo-missing-after-clone",
            duration_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
        )

    # 3. Parse properdocs.yml → nav tree.
    nav_config = nav.parse_properdocs_yml(repo_root)
    if nav_config is None:
        # Fall back to alphabetical — no nav tree to persist.
        nav_nodes: list[nav.NavNode] = []
        docs_dir_rel = "Docs"
    else:
        nav_nodes = nav_config.nav
        docs_dir_rel = nav_config.docs_dir

    # Persist the nav tree (so the sidebar can render without re-parsing YAML).
    if nav_nodes:
        try:
            store.replace_nav_tree([n.to_dict() for n in nav_nodes])
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to persist nav tree: %s", exc)

    # 4. Walk the Docs/ directory and index each Markdown file.
    docs_root = repo_root / docs_dir_rel
    if not docs_root.is_dir():
        _log.error("Docs directory not found at %s", docs_root)
        return SyncResult(
            success=False,
            error=f"docs-dir-missing: {docs_root}",
            duration_seconds=(datetime.now(timezone.utc) - started).total_seconds(),
        )

    indexed = 0
    found_paths: set[str] = set()
    for md_path in sorted(docs_root.rglob("*.md")):
        rel_path = md_path.relative_to(docs_root).as_posix()
        found_paths.add(rel_path)
        try:
            _index_one(md_path, rel_path, repo_root, nav_nodes)
            indexed += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to index %s: %s", rel_path, exc)

    # 5. Soft-delete documents that have disappeared.
    deleted = store.mark_missing_deleted(found_paths)

    # 6. Record the last-sync metadata.
    finished = datetime.now(timezone.utc)
    last_sync_path().parent.mkdir(parents=True, exist_ok=True)
    last_sync_path().write_text(
        json.dumps({
            "synced_at": finished.isoformat(),
            "documents_indexed": indexed,
            "documents_deleted": deleted,
            "documents_total": store.document_count(),
            "repo_url": DOCS_REPO_URL,
        }, indent=2),
        encoding="utf-8",
    )

    duration = (finished - started).total_seconds()
    _log.info(
        "Sync complete: %d indexed, %d deleted, %d total (%.1fs)",
        indexed, deleted, store.document_count(), duration,
    )
    return SyncResult(
        success=True,
        documents_indexed=indexed,
        documents_deleted=deleted,
        duration_seconds=duration,
    )


# ----------------------------------------------------------------------
# Repo management
# ----------------------------------------------------------------------
def _ensure_repo_cloned(force: bool = False) -> None:
    """Clone the docs repo if missing, or pull if it already exists."""
    repo_root = docs_repo_root()

    if force and repo_root.exists():
        _log.info("Force re-clone: removing %s", repo_root)
        shutil.rmtree(repo_root, ignore_errors=True)

    if not repo_root.exists():
        _log.info("Cloning %s → %s", DOCS_REPO_URL, repo_root)
        repo_root.parent.mkdir(parents=True, exist_ok=True)
        _run_git(
            ["clone", "--depth", "1", DOCS_REPO_URL, str(repo_root)],
            cwd=repo_root.parent,
        )
        return

    # Already cloned — try to pull. If pull fails (network error, etc.),
    # log and continue; we'll use whatever is on disk.
    _log.info("Pulling latest changes in %s", repo_root)
    try:
        _run_git(["fetch", "--depth", "1", "origin", "master"], cwd=repo_root)
        _run_git(["reset", "--hard", "origin/master"], cwd=repo_root)
    except Exception as exc:  # noqa: BLE001
        _log.warning("git pull failed (continuing with existing checkout): %s", exc)


def _run_git(args: list[str], cwd: Path, timeout: int = 120) -> str:
    """Run a git subprocess and return stdout. Raises on non-zero exit."""
    cmd = ["git"] + args
    _log.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"git command failed: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
    return result.stdout


# ----------------------------------------------------------------------
# Per-document indexing
# ----------------------------------------------------------------------
_MD_BUTTON_RE = re.compile(r"\{[^}]*\.md-button[^}]*\}")
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _index_one(
    md_path: Path,
    rel_path: str,
    repo_root: Path,
    nav_nodes: list[nav.NavNode],
) -> None:
    """Read a single Markdown file and upsert it into the store."""
    raw = md_path.read_text(encoding="utf-8", errors="replace")

    # Strip MkDocs attribute braces ({ .md-button ... }) and front matter
    content = _normalise_markdown(raw)

    # Title: prefer properdocs.yml nav title, fall back to first H1, fall back to filename
    title = nav.title_for_path(nav_nodes, rel_path)
    if not title:
        title = _extract_title(content) or md_path.stem.replace("-", " ").replace("_", " ")

    # Category: top-level nav group containing this path, or first path segment
    category = _category_for_path(rel_path, nav_nodes)

    # Last-modified: try git log, fall back to file mtime
    last_modified = _git_last_modified(repo_root, md_path)

    # Source URLs (for the "View source on GitHub" / "Open on docs site" links)
    source_repo_url = DOCS_REPO_BLOB + "Docs/" + rel_path
    # The docs site URL is the .html version of the path
    source_url = DOCS_SITE + rel_path[:-3] + ".html" if rel_path.endswith(".md") else DOCS_SITE

    store.upsert_document(
        file_path=rel_path,
        title=title,
        category=category,
        content=content,
        file_last_modified=last_modified,
        source_url=source_url,
        source_repo_url=source_repo_url,
    )


def _normalise_markdown(md: str) -> str:
    """Strip MkDocs attribute braces and front matter. Return the body."""
    # Strip front matter (YAML between --- markers at the start)
    fm_match = _FRONT_MATTER_RE.match(md)
    if fm_match:
        md = md[fm_match.end():]
    # Strip trailing attribute braces like { .md-button .md-button--primary }
    md = _MD_BUTTON_RE.sub("", md)
    # Trim excessive blank lines
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md


def _extract_title(md: str) -> str | None:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return None


def _category_for_path(rel_path: str, nav_nodes: list[nav.NavNode]) -> str:
    """Find the top-level nav group containing ``rel_path``.

    Falls back to the first path segment (e.g. "Install" for
    "Install/Foo.md") if the path isn't in the nav tree.
    """
    for n in nav_nodes:
        for descendant in nav.walk(n):
            if descendant.path and not descendant.is_external:
                p = descendant.path.lstrip("./").lstrip("/")
                if p == rel_path:
                    return n.title
    # Fallback: first path segment
    parts = rel_path.split("/")
    if len(parts) > 1:
        return parts[0]
    return "root"


def _git_last_modified(repo_root: Path, file_path: Path) -> str:
    """Return the last-commit date of ``file_path`` as an ISO 8601 string."""
    try:
        rel = file_path.relative_to(repo_root).as_posix()
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", rel],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    # Fallback: file mtime
    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return ""


# ----------------------------------------------------------------------
# Public helpers used by the UI
# ----------------------------------------------------------------------
def last_sync_info() -> dict:
    """Return the last-sync metadata, or an empty dict if never synced."""
    p = last_sync_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def last_sync_display() -> str:
    """Human-readable "last synced" string for the header."""
    info = last_sync_info()
    if not info:
        return "Never synced"
    synced_at = info.get("synced_at", "")
    if not synced_at:
        return "Never synced"
    try:
        dt = datetime.fromisoformat(synced_at)
        return "Synced " + dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "Synced " + synced_at[:16]
