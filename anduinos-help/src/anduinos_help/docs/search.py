"""Search index over the documentation catalog.

Builds an in-memory inverted index from article titles, headings, body
text, tags, and code blocks. Supports prefix, substring, and fuzzy
matches via :mod:`rapidfuzz`. Also indexes terminal commands so users
can search by command name (``apt``, ``free``, ``systemctl``) and find
relevant articles.

The first search query triggers a lazy build; subsequent searches are
fast.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz

from anduinos_help.docs import loader, parser
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
    context: str  # surrounding paragraph or block text
    score: float


# Tokenizer: split on non-word boundaries, lowercase.
# Hyphens inside words are stripped so "Wi-Fi" and "wifi" both tokenize
# to "wifi" — important because docs use "Wi-Fi" but users type "wifi".
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|[A-Za-z]+")
_TAG_RE = re.compile(r"\{[^}]*\}")  # MkDocs attribute braces


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    text = _TAG_RE.sub(" ", text)
    return [t.lower().replace("-", "") for t in _TOKEN_RE.findall(text)]


@dataclass
class _IndexEntry:
    article_id: str
    title: str
    title_tokens: list[str]
    heading_tokens: list[str]
    body_tokens: list[str]
    code_tokens: list[str]
    tag_tokens: list[str]
    summary: str
    category: str
    subcategory: str | None
    commands: list[tuple[str, str | None, str]]  # (command, lang, context)


class SearchIndex:
    def __init__(self) -> None:
        self._entries: dict[str, _IndexEntry] = {}
        self._built = False

    def build(self) -> None:
        cat = loader.load_catalog()
        for art in cat.articles.values():
            self._index_article(art)
        self._built = True
        _log.info("Search index built: %d articles", len(self._entries))

    def _index_article(self, art: loader.Article) -> None:
        md = loader.load_markdown(art)
        if not md:
            return
        try:
            blocks = parser.parse(md)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to parse %s for search: %s", art.id, exc)
            return

        headings: list[str] = []
        body: list[str] = []
        code: list[str] = []
        commands: list[tuple[str, str | None, str]] = []

        def walk(blks: list[dict]) -> None:
            for b in blks:
                t = b["type"]
                if t == "heading":
                    headings.append(b["text"])
                elif t == "paragraph":
                    body.append(b.get("text", parser.render_spans_plain(b.get("spans", []))))
                elif t == "code":
                    code.append(b["text"])
                    cmds = _extract_commands(b["text"], b.get("language"))
                    for cmd in cmds:
                        commands.append((cmd, b.get("language"), b["text"][:240]))
                elif t == "list":
                    for item in b["items"]:
                        walk(item)
                elif t == "blockquote" or t == "callout":
                    walk(b.get("blocks", []))

        walk(blocks)

        title_tokens = tokenize(art.title)
        heading_tokens = tokenize(" ".join(headings))
        body_tokens = tokenize(" ".join(body))
        code_tokens = tokenize(" ".join(code))
        tag_tokens = tokenize(" ".join(art.tags))

        self._entries[art.id] = _IndexEntry(
            article_id=art.id,
            title=art.title,
            title_tokens=title_tokens,
            heading_tokens=heading_tokens,
            body_tokens=body_tokens,
            code_tokens=code_tokens,
            tag_tokens=tag_tokens,
            summary=art.summary,
            category=art.category,
            subcategory=art.subcategory,
            commands=commands,
        )

    # ------------------------------------------------------------------
    # search API
    # ------------------------------------------------------------------
    def search(self, query: str, limit: int = 20, min_score: float = 30.0) -> list[SearchResult]:
        if not query.strip():
            return []
        if not self._built:
            self.build()
        q = query.strip()
        q_tokens = tokenize(q)
        q_lower = q.lower()
        results: dict[str, SearchResult] = {}

        for entry in self._entries.values():
            score = 0.0
            matched_fields: list[str] = []
            matched_keywords: set[str] = set()

            # Title: strongest signal
            title_lower = entry.title.lower()
            if q_lower in title_lower:
                score += 100.0
                matched_fields.append("title")
            else:
                t_ratio = fuzz.partial_ratio(q_lower, title_lower)
                if t_ratio >= 70:
                    score += t_ratio * 0.6
                    if t_ratio >= 85:
                        matched_fields.append("title")

            # Tag exact match
            for tok in q_tokens:
                if tok in entry.tag_tokens:
                    score += 35.0
                    matched_keywords.add(tok)
                    if "tags" not in matched_fields:
                        matched_fields.append("tags")

            # Heading match
            for tok in q_tokens:
                if tok in entry.heading_tokens:
                    score += 22.0
                    matched_keywords.add(tok)
                    if "headings" not in matched_fields:
                        matched_fields.append("headings")

            # Body match (token overlap)
            body_set = set(entry.body_tokens)
            body_hits = sum(1 for tok in q_tokens if tok in body_set)
            if body_hits:
                score += 18.0 * body_hits / max(1, len(q_tokens)) * (1.0 if body_hits == len(q_tokens) else 0.6)
                matched_fields.append("body")
                for tok in q_tokens:
                    if tok in body_set:
                        matched_keywords.add(tok)

            # Code match (commands and config)
            code_set = set(entry.code_tokens)
            code_hits = sum(1 for tok in q_tokens if tok in code_set)
            if code_hits:
                score += 12.0 * code_hits / max(1, len(q_tokens))
                if "code" not in matched_fields:
                    matched_fields.append("code")

            # Fuzzy whole-query fallback
            if score < min_score:
                body_text = " ".join(entry.body_tokens)
                ratio = fuzz.partial_ratio(q_lower, body_text[:4096])
                if ratio >= 70:
                    score = max(score, ratio * 0.35)
                    matched_fields.append("body")

            if score >= min_score:
                results[entry.article_id] = SearchResult(
                    article_id=entry.article_id,
                    title=entry.title,
                    category=entry.category,
                    subcategory=entry.subcategory,
                    summary=entry.summary or _build_summary(entry),
                    score=score,
                    matched_fields=sorted(set(matched_fields)),
                    matched_keywords=sorted(matched_keywords),
                )

        ranked = sorted(results.values(), key=lambda r: (-r.score, r.title.lower()))
        return ranked[:limit]

    def search_commands(self, query: str, limit: int = 10) -> list[CommandResult]:
        if not query.strip():
            return []
        if not self._built:
            self.build()
        q = query.strip().lower()
        results: list[CommandResult] = []
        for entry in self._entries.values():
            for cmd, lang, ctx in entry.commands:
                # Score by how well the query matches the command line
                cmd_lower = cmd.lower()
                if q in cmd_lower:
                    score = 100.0 - abs(len(cmd_lower) - len(q)) * 0.5
                else:
                    ratio = fuzz.partial_ratio(q, cmd_lower)
                    if ratio < 65:
                        continue
                    score = ratio * 0.7
                results.append(CommandResult(
                    article_id=entry.article_id,
                    article_title=entry.title,
                    command=cmd,
                    language=lang,
                    context=ctx,
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


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
_CMD_PROMPT_RE = re.compile(r"^(?:\$|#|>)\s*(.+)$")
_CMD_LINE_RE = re.compile(r"^(?:sudo\s+|apt(?:-get)?\s+|systemctl\s+|journalctl\s+|dpkg\s+|flatpak\s+|snap\s+|nix\s+|git\s+|ls\s+|cd\s+|cat\s+|grep\s+|find\s+|chmod\s+|chown\s+|mount\s+|umount\s+|fdisk\s+|lsblk\s+|blkid\s+|dd\s+|rsync\s+|tar\s+|uname\s+|free\s+|df\s+|du\s+|top\s+|htop\s+|btop\s+|ps\s+|kill\s+|ip\s+|nmcli\s+|nmtui\s+|ufw\s+|ssh\s+|scp\s+|rsync\s+|curl\s+|wget\s+|dmesg\s+|lspci\s+|lsusb\s+|aptitude\s+|gpg\s+|openssl\s+|cryptsetup\s+|modprobe\s+|insmod\s+|rmmod\s+|service\s+|hostnamectl\s+|localectl\s+|timedatectl\s+|loginctl\s+|machinectl\s+|hostname\s+|whoami\s+|id\s+|env\s+|export\s+|echo\s+|printf\s+|test\s+|\[).*$")


def _extract_commands(code: str, language: str | None) -> list[str]:
    """Extract command lines from a code block.

    Returns the *command itself* (without the prompt) so it's directly
    copyable. For shell blocks with no prompt, the whole block is treated
    as commands.
    """
    is_shell = (language or "").lower() in {"bash", "sh", "shell", "console", "shell-session", "zsh"} or language is None
    if not is_shell:
        return []
    out: list[str] = []
    for line in code.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            if s.startswith("#") and not s.startswith("#!"):
                # Root prompt, not a comment
                cmd = s[1:].strip()
                if cmd and not cmd.startswith("#"):
                    out.append(cmd)
            continue
        m = _CMD_PROMPT_RE.match(s)
        if m:
            out.append(m.group(1).strip())
            continue
        # Shell line without prompt
        if _CMD_LINE_RE.match(s):
            out.append(s)
            continue
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c in seen:
            continue
        seen.add(c)
        deduped.append(c)
    return deduped


def _build_summary(entry: _IndexEntry) -> str:
    """Generate a short summary from indexed body tokens when missing."""
    if not entry.body_tokens:
        return ""
    # First ~20 words joined back together
    return " ".join(entry.body_tokens[:30])[:200].capitalize()


# Singleton
_index: SearchIndex | None = None


def get_index() -> SearchIndex:
    global _index
    if _index is None:
        _index = SearchIndex()
    return _index


def search(query: str, limit: int = 20) -> list[SearchResult]:
    return get_index().search(query, limit=limit)


def search_commands(query: str, limit: int = 10) -> list[CommandResult]:
    return get_index().search_commands(query, limit=limit)
