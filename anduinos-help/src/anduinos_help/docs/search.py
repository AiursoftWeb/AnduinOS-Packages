"""Documentation search — thin wrapper over the SQLite FTS5 index.

The original flat-file version built an in-memory inverted index with
rapidfuzz. Now that documents live in SQLite with an FTS5 virtual
table (see :mod:`anduinos_help.docs.store`), search is just a thin
wrapper around :func:`store.search`. We keep the same public API
(``search()` / ``search_commands()``) so the existing UI doesn't have
to change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from anduinos_help.docs import loader, store
from anduinos_help.utils.logging import get_logger

_log = get_logger("docs.search")


@dataclass
class SearchResult:
    article_id: str
    title: str
    category: str
    subcategory: str | None
    summary: str
    score: float
    matched_fields: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)

    @property
    def display_category(self) -> str:
        return loader.CATEGORY_DISPLAY.get(self.category, self.category.title())


@dataclass
class CommandResult:
    article_id: str
    article_title: str
    command: str
    language: str | None
    context: str
    score: float


def search(query: str, limit: int = 20) -> list[SearchResult]:
    """Full-text search across title + content via SQLite FTS5."""
    q = (query or "").strip()
    if not q:
        return []
    cat = loader.load_catalog()
    fts_results = store.search(q, limit=limit)
    out: list[SearchResult] = []
    for r in fts_results:
        # Look up the article to get subcategory / summary
        art = cat.get(r.article_id)
        sub = art.subcategory if art else None
        summary = art.summary if art and art.summary else r.snippet
        out.append(SearchResult(
            article_id=r.article_id,
            title=r.title,
            category=r.category,
            subcategory=sub,
            summary=summary,
            score=r.rank,
            matched_fields=["content"],
            matched_keywords=q.split(),
        ))
    return out


# Command-extraction search (kept from the original implementation).
# This walks the parsed blocks of each article looking for shell commands
# in code blocks that match the query.
_CMD_PROMPT_RE = re.compile(r"^(?:\$|#|>)\s*(.+)$")
_CMD_LINE_RE = re.compile(
    r"^(?:sudo\s+|apt(?:-get)?\s+|systemctl\s+|journalctl\s+|dpkg\s+|flatpak\s+|"
    r"snap\s+|nix\s+|git\s+|ls\s+|cd\s+|cat\s+|grep\s+|find\s+|chmod\s+|chown\s+|"
    r"mount\s+|umount\s+|fdisk\s+|lsblk\s+|blkid\s+|dd\s+|rsync\s+|tar\s+|uname\s+|"
    r"free\s+|df\s+|du\s+|top\s+|htop\s+|btop\s+|ps\s+|kill\s+|ip\s+|nmcli\s+|nmtui\s+|"
    r"ufw\s+|ssh\s+|scp\s+|curl\s+|wget\s+|dmesg\s+|lspci\s+|lsusb\s+|aptitude\s+|"
    r"gpg\s+|openssl\s+|cryptsetup\s+|modprobe\s+|service\s+|hostnamectl\s+|"
    r"localectl\s+|timedatectl\s+|loginctl\s+|machinectl\s+|hostname\s+|whoami\s+|"
    r"id\s+|env\s+|export\s+|echo\s+|printf\s+|test\s+|\[).*$"
)


def _extract_commands(code: str, language: str | None) -> list[str]:
    is_shell = (language or "").lower() in {
        "bash", "sh", "shell", "console", "shell-session", "zsh"
    } or language is None
    if not is_shell:
        return []
    out: list[str] = []
    for line in code.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            if s.startswith("#") and not s.startswith("#!"):
                cmd = s[1:].strip()
                if cmd and not cmd.startswith("#"):
                    out.append(cmd)
            continue
        m = _CMD_PROMPT_RE.match(s)
        if m:
            out.append(m.group(1).strip())
            continue
        if _CMD_LINE_RE.match(s):
            out.append(s)
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c in seen:
            continue
        seen.add(c)
        deduped.append(c)
    return deduped


def search_commands(query: str, limit: int = 10) -> list[CommandResult]:
    """Search for shell commands matching ``query`` across all articles.

    This is more expensive than :func:`search` (it walks the parsed
    blocks of every article) so it's only called for the "Matching
    terminal commands" section of the search page. The work is
    bounded by ``limit``.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    cat = loader.load_catalog()
    results: list[CommandResult] = []
    from anduinos_help.docs import parser as doc_parser
    for art in list(cat.articles.values())[:200]:  # bound the work
        md = loader.load_markdown(art)
        if not md:
            continue
        try:
            blocks = doc_parser.parse(md)
        except Exception:  # noqa: BLE001
            continue
        for block in blocks:
            if block.get("type") != "code":
                continue
            cmds = _extract_commands(block.get("text", ""), block.get("language"))
            for cmd in cmds:
                cmd_lower = cmd.lower()
                if q in cmd_lower:
                    score = 100.0 - abs(len(cmd_lower) - len(q)) * 0.5
                elif _fuzzy_partial(q, cmd_lower) >= 65:
                    score = _fuzzy_partial(q, cmd_lower) * 0.7
                else:
                    continue
                results.append(CommandResult(
                    article_id=art.id,
                    article_title=art.title,
                    command=cmd,
                    language=block.get("language"),
                    context=block.get("text", "")[:240],
                    score=score,
                ))
    results.sort(key=lambda r: (-r.score, r.command.lower()))
    # Deduplicate by (article_id, command)
    seen: set[tuple[str, str]] = set()
    out: list[CommandResult] = []
    for r in results:
        key = (r.article_id, r.command)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _fuzzy_partial(needle: str, haystack: str) -> float:
    """Cheap fuzzy substring match — returns a 0-100 score.

    We avoid the rapidfuzz dependency by using a simple ratio of
    matching characters. This is good enough for command search where
    the user typically types a prefix or exact command name.
    """
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 100.0
    # Subsequence match
    i = 0
    for c in haystack:
        if i < len(needle) and c == needle[i]:
            i += 1
    if i == len(needle):
        return 80.0
    return 0.0
