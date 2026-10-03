"""SQLite-backed document store.

Replaces the original flat-file ``assets/articles/`` + ``assets/index.json``
catalog with a single SQLite database under ``XDG_DATA_HOME``. The
schema mirrors the AnduinOS DocsViewer ``Document`` entity:

* ``documents``  — one row per indexed Markdown file
* ``nav_entries`` — ordered sidebar tree (parsed from ``properdocs.yml``)
* ``meta``       — key/value table for sync metadata (last-sync time, etc.)

The store is intentionally thin: it exposes a small set of typed
helpers (``upsert_document``, ``get_document``, ``list_documents``,
``search_documents``, ``nav_tree``) so the rest of the app doesn't
have to know any SQL.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from anduinos_help.utils.logging import get_logger
from anduinos_help.utils.paths import database_path

_log = get_logger("docs.store")

# Schema version — bump + add a migration if the schema changes.
_SCHEMA_VERSION = 1


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path          TEXT NOT NULL UNIQUE,  -- repo-relative, e.g. "Install/Download-AnduinOS.md"
    title              TEXT NOT NULL,         -- from nav tree or filename
    category           TEXT NOT NULL,         -- top-level nav group, e.g. "Install"
    content            TEXT NOT NULL DEFAULT '',
    file_last_modified TEXT NOT NULL DEFAULT '',  -- ISO 8601, from git log
    source_url         TEXT NOT NULL DEFAULT '',  -- docs.anduinos.com URL
    source_repo_url    TEXT NOT NULL DEFAULT '',  -- GitHub blob URL
    is_deleted         INTEGER NOT NULL DEFAULT 0,
    indexed_at         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_documents_is_deleted ON documents(is_deleted);
CREATE INDEX IF NOT EXISTS idx_documents_title ON documents(title);

-- FTS5 full-text index over title + content. Updated via triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    content,
    content='documents',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, content)
    VALUES (new.id, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content)
    VALUES ('delete', old.id, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content)
    VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO documents_fts(rowid, title, content)
    VALUES (new.id, new.title, new.content);
END;

CREATE TABLE IF NOT EXISTS nav_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    title       TEXT NOT NULL,
    path        TEXT,                  -- repo-relative .md path, or external URL, or NULL for groups
    is_external INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(parent_id) REFERENCES nav_entries(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_nav_parent ON nav_entries(parent_id, sort_order);
"""


@dataclass(frozen=True)
class Document:
    """A single indexed Markdown document."""
    id: int
    file_path: str
    title: str
    category: str
    content: str
    file_last_modified: str
    source_url: str
    source_repo_url: str
    is_deleted: bool

    @property
    def slug(self) -> str:
        """URL-friendly slug derived from the file path.

        e.g. ``Install/Download-AnduinOS.md`` → ``install-download-anduinos``
        """
        stem = self.file_path.rsplit("/", 1)[-1]
        if stem.endswith(".md"):
            stem = stem[:-3]
        return stem.lower().replace("_", "-")

    @property
    def article_id(self) -> str:
        """Stable id used by the UI for navigation history.

        We use the file_path without the .md extension, lowercased —
        e.g. ``install/download-anduinos``.
        """
        path = self.file_path
        if path.endswith(".md"):
            path = path[:-3]
        return path.lower()


@dataclass
class NavEntry:
    """A node in the sidebar navigation tree."""
    id: int
    parent_id: int | None
    sort_order: int
    title: str
    path: str | None         # .md path (internal) / URL (external) / None (group)
    is_external: bool
    is_group: bool
    children: list["NavEntry"] = field(default_factory=list)


# Module-level lock — sqlite3 connections aren't shareable across threads,
# so we open a fresh connection per call (cheap) but serialise writes.
_lock = threading.Lock()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    db = database_path()
    conn = sqlite3.connect(str(db), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create the schema if it doesn't exist and stamp the schema version."""
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )


def set_meta(key: str, value: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, value),
        )


def get_meta(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


# ----------------------------------------------------------------------
# Documents
# ----------------------------------------------------------------------
def upsert_document(
    file_path: str,
    title: str,
    category: str,
    content: str,
    file_last_modified: str,
    source_url: str = "",
    source_repo_url: str = "",
) -> None:
    """Insert or update a single document."""
    indexed_at = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO documents
                (file_path, title, category, content, file_last_modified,
                 source_url, source_repo_url, is_deleted, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                title              = excluded.title,
                category           = excluded.category,
                content            = excluded.content,
                file_last_modified = excluded.file_last_modified,
                source_url         = excluded.source_url,
                source_repo_url    = excluded.source_repo_url,
                is_deleted         = 0,
                indexed_at         = excluded.indexed_at
            """,
            (file_path, title, category, content, file_last_modified,
             source_url, source_repo_url, indexed_at),
        )


def mark_missing_deleted(found_paths: set[str]) -> int:
    """Soft-delete documents whose ``file_path`` is not in ``found_paths``.

    Returns the number of newly-deleted documents.
    """
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            UPDATE documents
               SET is_deleted = 1
             WHERE is_deleted = 0
               AND file_path NOT IN (
                   SELECT value FROM json_each(?)
               )
            """,
            (json.dumps(sorted(found_paths)),),
        )
        return cur.rowcount or 0


def get_document_by_path(file_path: str) -> Document | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE file_path = ? AND is_deleted = 0",
            (file_path,),
        ).fetchone()
        return _row_to_document(row) if row else None


def get_document_by_id(article_id: str) -> Document | None:
    """Look up a document by its UI ``article_id`` (lowercased path without .md)."""
    # Normalise to file_path form
    file_path = article_id.replace("\\", "/")
    if not file_path.endswith(".md"):
        file_path = file_path + ".md"
    # Capitalize path segments the way the repo stores them
    # (e.g. "install/download-anduinos" → "Install/Download-AnduinOS.md")
    # We can't reverse a slug reliably, so try case-insensitive match.
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE lower(replace(file_path, '.md', '')) = ? AND is_deleted = 0",
            (file_path[:-3].lower(),),
        ).fetchone()
        return _row_to_document(row) if row else None


def list_documents(category: str | None = None) -> list[Document]:
    """List all live documents, optionally filtered by category."""
    with _connect() as conn:
        if category is None:
            rows = conn.execute(
                "SELECT * FROM documents WHERE is_deleted = 0 ORDER BY title"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents WHERE is_deleted = 0 AND category = ? ORDER BY title",
                (category,),
            ).fetchall()
        return [_row_to_document(r) for r in rows]


def list_categories() -> list[tuple[str, int]]:
    """Return ``[(category, count), ...]`` for live documents, ordered by count desc."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT category, COUNT(*) AS cnt
              FROM documents
             WHERE is_deleted = 0
             GROUP BY category
             ORDER BY cnt DESC, category ASC
            """
        ).fetchall()
        return [(r["category"], r["cnt"]) for r in rows]


def document_count() -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE is_deleted = 0"
        ).fetchone()
        return row["n"] if row else 0


# ----------------------------------------------------------------------
# Full-text search (FTS5)
# ----------------------------------------------------------------------
@dataclass
class SearchResult:
    article_id: str
    title: str
    category: str
    snippet: str
    rank: float


def search(query: str, limit: int = 50) -> list[SearchResult]:
    """FTS5 search over title + content.

    Returns ranked results with a snippet of the matching content.
    """
    q = (query or "").strip()
    if not q:
        return []
    # FTS5 query syntax: tokenize and join with OR + prefix matching
    # so "wifi driver" matches docs containing either term.
    terms = [t for t in q.split() if t]
    if not terms:
        return []
    fts_query = " OR ".join(f'"{t}"*' for t in terms)  # prefix match per token
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT d.id,
                   d.file_path,
                   d.title,
                   d.category,
                   snippet(documents_fts, 1, '<b>', '</b>', '…', 24) AS snippet,
                   bm25(documents_fts) AS rank
              FROM documents_fts
              JOIN documents d ON d.id = documents_fts.rowid
             WHERE documents_fts MATCH ?
               AND d.is_deleted = 0
             ORDER BY rank
             LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return [
            SearchResult(
                article_id=_path_to_article_id(r["file_path"]),
                title=r["title"],
                category=r["category"],
                snippet=r["snippet"] or "",
                rank=-r["rank"],  # bm25 returns negative scores (lower = better)
            )
            for r in rows
        ]


# ----------------------------------------------------------------------
# Navigation tree (parsed from properdocs.yml, stored ordered)
# ----------------------------------------------------------------------
def replace_nav_tree(entries: list[dict]) -> None:
    """Replace the entire nav_entries table with ``entries``.

    ``entries`` is a list of dicts with keys: ``title``, ``path`` (optional),
    ``is_external``, ``is_group``, ``children`` (recursive).
    """
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM nav_entries")

        def _insert(entries: list[dict], parent_id: int | None) -> None:
            for i, e in enumerate(entries):
                cur = conn.execute(
                    """
                    INSERT INTO nav_entries (parent_id, sort_order, title, path, is_external)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (parent_id, i, e["title"], e.get("path"), 1 if e.get("is_external") else 0),
                )
                new_id = cur.lastrowid
                if e.get("children"):
                    _insert(e["children"], new_id)

        _insert(entries, None)


def nav_tree() -> list[NavEntry]:
    """Return the full navigation tree, top-level entries first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM nav_entries ORDER BY parent_id IS NULL DESC, parent_id ASC, sort_order ASC"
        ).fetchall()
        # Build a dict of id → NavEntry, then link children.
        by_id: dict[int, NavEntry] = {}
        for r in rows:
            entry = NavEntry(
                id=r["id"],
                parent_id=r["parent_id"],
                sort_order=r["sort_order"],
                title=r["title"],
                path=r["path"],
                is_external=bool(r["is_external"]),
                is_group=(r["path"] is None and not bool(r["is_external"])),
            )
            by_id[entry.id] = entry
        roots: list[NavEntry] = []
        for entry in by_id.values():
            if entry.parent_id is None:
                roots.append(entry)
            else:
                parent = by_id.get(entry.parent_id)
                if parent:
                    parent.children.append(entry)
        return roots


def nav_entry_for_path(file_path: str) -> NavEntry | None:
    """Find the nav entry that points at ``file_path``."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM nav_entries WHERE path = ?",
            (file_path,),
        ).fetchone()
        if not row:
            return None
        return NavEntry(
            id=row["id"],
            parent_id=row["parent_id"],
            sort_order=row["sort_order"],
            title=row["title"],
            path=row["path"],
            is_external=bool(row["is_external"]),
            is_group=False,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _row_to_document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        file_path=row["file_path"],
        title=row["title"],
        category=row["category"],
        content=row["content"],
        file_last_modified=row["file_last_modified"],
        source_url=row["source_url"],
        source_repo_url=row["source_repo_url"],
        is_deleted=bool(row["is_deleted"]),
    )


def _path_to_article_id(file_path: str) -> str:
    p = file_path
    if p.endswith(".md"):
        p = p[:-3]
    return p.lower()
