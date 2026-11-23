"""Markdown parser → structured intermediate representation.

The output is a list of IR blocks. Each block is a dict with ``type`` and
type-specific fields. The article renderer (in ``app/ui/article_view.py``)
walks this list and emits GTK widgets.

Supported block types
---------------------
heading        {"level": 1-6, "text": str, "id": str}
paragraph      {"text": str, "spans": [inline spans]}
code           {"language": str|None, "text": str, "is_root": bool}
list           {"ordered": bool, "items": [[block, ...], ...]}
blockquote     {"blocks": [...]}
table          {"headers": [str], "rows": [[str], ...]}
hr             {}
callout        {"kind": "note|warning|tip|danger", "title": str|None, "blocks": [...]}
image          {"src": str, "alt": str, "title": str|None}
html_block     {"text": str}     # passed through, not rendered

Inline spans (inside paragraph.text uses plain text + spans list):
text           {"text": str}
code           {"text": str}
link            {"url": str, "text": str, "internal_id": str|None}
strong          {"text": str}
em              {"text": str}
br              {}
"""
from __future__ import annotations

import html
import re
import uuid
from typing import Any, Protocol

from markdown_it import MarkdownIt
from markdown_it.token import Token

CALLOUT_RE = re.compile(r"^(?:\[!(note|tip|warning|caution|danger|info)\]\s*)(.*)$", re.IGNORECASE)
MD_BUTTON_RE = re.compile(r"\{[^}]*\.md-button[^}]*\}\s*$")


class LinkResolver(Protocol):
    """Resolves a Markdown href to an internal article id.

    Implementations look up the href against the documentation catalog,
    resolving relative ``.md`` / ``.html`` references to sibling articles
    using the current article's source path.
    """

    def resolve(self, href: str) -> str | None:
        """Return the article id for ``href`` or None if it is not internal."""
        ...


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s or "section"


def _strip_attrs_in_braces(text: str) -> str:
    """Strip trailing ``{ .md-button ... }`` brace attributes MkDocs uses."""
    return MD_BUTTON_RE.sub("", text).rstrip()


class DocParser:
    """Parse Markdown into a structured IR.

    The parser is stateless after construction; safe to reuse across threads.
    However, ``parse()`` temporarily stores a ``link_resolver`` on the
    instance for the duration of a single parse call, so concurrent calls
    on the same instance are NOT safe. Use a separate instance per thread
    or call ``parse()`` from the main thread only.
    """

    def __init__(self) -> None:
        self._md = (
            MarkdownIt("commonmark", {"html": True, "linkify": True, "breaks": False})
            .enable("table")
            .enable("strikethrough")
        )
        self._link_resolver: "LinkResolver | None" = None

    def parse(self, markdown: str, link_resolver: "LinkResolver | None" = None) -> list[dict[str, Any]]:
        """Return a list of IR blocks.

        If ``link_resolver`` is provided, it is used to rewrite relative
        ``.md`` / ``.html`` hrefs into ``anduinos-help://article/<id>`` URIs
        so the renderer can dispatch them as in-app navigation.
        """
        if not markdown:
            return []
        self._link_resolver = link_resolver
        tokens = self._md.parse(markdown, {})
        blocks, _ = self._walk(tokens, 0)
        return self._merge_callouts(blocks)

    # ------------------------------------------------------------------
    # token walking
    # ------------------------------------------------------------------
    def _walk(self, tokens: list[Token], idx: int, end_token: str | None = None) -> tuple[list[dict], int]:
        blocks: list[dict[str, Any]] = []
        while idx < len(tokens):
            tok = tokens[idx]
            if end_token and tok.type == end_token:
                return blocks, idx + 1
            if tok.type == "heading_open":
                level = int(tok.tag[1])
                inline_tok = tokens[idx + 1]
                text = self._render_inline(inline_tok)
                # Also collect a structured spans list for richer rendering
                spans = self._extract_spans(inline_tok)
                blocks.append({
                    "type": "heading",
                    "level": level,
                    "text": text,
                    "spans": spans,
                    "id": _slugify(text),
                })
                idx += 3  # heading_open, inline, heading_close
            elif tok.type == "paragraph_open":
                inline_tok = tokens[idx + 1]
                text = self._render_inline(inline_tok)
                spans = self._extract_spans(inline_tok)
                blocks.append({
                    "type": "paragraph",
                    "text": text,
                    "spans": spans,
                })
                idx += 3
            elif tok.type == "fence" or tok.type == "code_block":
                lang = tok.info.strip() if tok.info else None
                blocks.append({
                    "type": "code",
                    "language": lang,
                    "text": tok.content.rstrip("\n"),
                    "is_root": _looks_like_root_prompt(tok.content),
                })
                idx += 1
            elif tok.type == "bullet_list_open" or tok.type == "ordered_list_open":
                ordered = tok.type == "ordered_list_open"
                items: list[list[dict]] = []
                idx += 1
                while idx < len(tokens) and tokens[idx].type != f"list_item_close":
                    if tokens[idx].type == "list_item_open":
                        item_blocks, idx = self._walk(tokens, idx + 1, "list_item_close")
                        items.append(item_blocks)
                    else:
                        idx += 1
                if idx < len(tokens):
                    idx += 1  # list_close
                blocks.append({"type": "list", "ordered": ordered, "items": items})
            elif tok.type == "blockquote_open":
                inner_blocks, idx = self._walk(tokens, idx + 1, "blockquote_close")
                blocks.append({"type": "blockquote", "blocks": inner_blocks})
            elif tok.type == "hr":
                blocks.append({"type": "hr"})
                idx += 1
            elif tok.type == "table_open":
                headers: list[str] = []
                rows: list[list[str]] = []
                idx += 1
                while idx < len(tokens) and tokens[idx].type != "table_close":
                    if tokens[idx].type == "th_open":
                        inline = tokens[idx + 1]
                        headers.append(self._render_inline(inline))
                        idx += 3
                    elif tokens[idx].type == "td_open":
                        # collect cells until end of row
                        if not rows or len(rows[-1]) == len(headers):
                            rows.append([])
                        inline = tokens[idx + 1]
                        rows[-1].append(self._render_inline(inline))
                        idx += 3
                    elif tokens[idx].type == "tr_close":
                        idx += 1
                    else:
                        idx += 1
                if idx < len(tokens):
                    idx += 1  # table_close
                blocks.append({"type": "table", "headers": headers, "rows": rows})
            elif tok.type == "html_block":
                blocks.append({"type": "html_block", "text": tok.content})
                idx += 1
            elif tok.type == "inline":
                # stray inline token (shouldn't normally happen)
                idx += 1
            else:
                # Skip unknown opens/closes; pairs handled by _walk recursion
                idx += 1
        return blocks, idx

    # ------------------------------------------------------------------
    # inline rendering
    # ------------------------------------------------------------------
    def _render_inline(self, inline_tok: Token) -> str:
        """Render inline tokens to plain text for fallback display."""
        out: list[str] = []
        if inline_tok is None or not inline_tok.children:
            return ""
        for ch in inline_tok.children:
            if ch.type == "text":
                out.append(ch.content)
            elif ch.type == "code_inline":
                out.append(ch.content)
            elif ch.type == "softbreak" or ch.type == "hardbreak":
                out.append(" ")
            elif ch.type == "image":
                alt = ""
                for c in (ch.children or []):
                    if c.type == "text":
                        alt += c.content
                out.append(alt)
            else:
                # Recurse for em/strong/link
                out.append(self._render_inline(ch))
        return "".join(out)

    def _extract_spans(self, inline_tok: Token) -> list[dict[str, Any]]:
        """Return a list of structured inline spans for richer rendering."""
        spans: list[dict[str, Any]] = []
        if inline_tok is None or not inline_tok.children:
            return spans
        # Walk inline tokens in order, tracking open styles. markdown-it
        # emits paired tokens (link_open/link_close, strong_open/strong_close)
        # with the text as siblings — not children — so we maintain a stack
        # of active styles and the current URL.
        style_stack: list[set[str]] = [set()]
        current_url: str | None = None
        url_stack: list[str | None] = [None]

        for ch in inline_tok.children:
            if ch.type == "text":
                spans.append({
                    "type": "text",
                    "text": ch.content,
                    "style": set(style_stack[-1]),
                    "url": current_url,
                })
            elif ch.type == "code_inline":
                spans.append({
                    "type": "code",
                    "text": ch.content,
                    "style": set(style_stack[-1]) | {"code"},
                    "url": current_url,
                })
            elif ch.type in ("softbreak", "hardbreak"):
                spans.append({"type": "br"})
            elif ch.type == "image":
                alt = "".join(c.content for c in (ch.children or []) if c.type == "text")
                spans.append({
                    "type": "image",
                    "src": ch.attrs.get("src", ""),
                    "alt": alt,
                    "title": ch.attrs.get("title"),
                })
            elif ch.type == "link_open":
                href = ch.attrs.get("href", "")
                # If the link is a relative .md/.html reference to another
                # bundled article, resolve it to an internal navigation URI.
                if self._link_resolver is not None and href:
                    resolved = self._link_resolver.resolve(href)
                    if resolved:
                        href = f"anduinos-help://article/{resolved}"
                current_url = href
                url_stack.append(href)
                style_stack.append(set(style_stack[-1]) | {"link"})
            elif ch.type == "link_close":
                if len(url_stack) > 1:
                    url_stack.pop()
                    current_url = url_stack[-1]
                if len(style_stack) > 1:
                    style_stack.pop()
            elif ch.type == "strong_open":
                style_stack.append(set(style_stack[-1]) | {"strong"})
            elif ch.type == "strong_close":
                if len(style_stack) > 1:
                    style_stack.pop()
            elif ch.type == "em_open":
                style_stack.append(set(style_stack[-1]) | {"em"})
            elif ch.type == "em_close":
                if len(style_stack) > 1:
                    style_stack.pop()
            elif ch.type == "s_open":
                style_stack.append(set(style_stack[-1]) | {"strike"})
            elif ch.type == "s_close":
                if len(style_stack) > 1:
                    style_stack.pop()
            elif ch.type == "code_inline":
                pass  # handled above (defensive duplicate branch)
        return spans

    # ------------------------------------------------------------------
    # callout merging
    # ------------------------------------------------------------------
    def _merge_callouts(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Detect blockquote callouts (``> [!WARNING] ...``) and convert them."""
        out: list[dict[str, Any]] = []
        for b in blocks:
            if b["type"] == "blockquote":
                callout = self._try_callout(b)
                out.append(callout if callout else b)
            elif b["type"] == "list":
                b["items"] = [self._merge_callouts(item) for item in b["items"]]
                out.append(b)
            elif b["type"] == "blockquote" or b.get("blocks"):
                b["blocks"] = self._merge_callouts(b["blocks"])
                out.append(b)
            else:
                out.append(b)
        return out

    def _try_callout(self, b: dict[str, Any]) -> dict[str, Any] | None:
        blocks = b.get("blocks", [])
        if not blocks:
            return None
        first = blocks[0]
        if first["type"] != "paragraph":
            return None
        text = first.get("text", "")
        m = CALLOUT_RE.match(text)
        if not m:
            return None
        kind = m.group(1).lower()
        if kind == "caution":
            kind = "warning"
        if kind == "info":
            kind = "note"
        # m.group(2) is the text after the [!KIND] marker (may include
        # the rest of the original paragraph).
        rest = m.group(2).strip()
        new_blocks = []
        if rest:
            new_blocks.append({
                "type": "paragraph",
                "text": rest,
                "spans": [{"type": "text", "text": rest, "style": set(), "url": None}],
            })
        new_blocks.extend(blocks[1:])
        return {"type": "callout", "kind": kind, "title": None, "blocks": new_blocks}


def _looks_like_root_prompt(code: str) -> bool:
    """Return True if a code block's first non-empty line starts with ``#`` (root prompt)."""
    for line in code.splitlines():
        s = line.strip()
        if not s:
            continue
        return s.startswith("#") and not s.startswith("#!")
    return False


def render_spans_plain(spans: list[dict[str, Any]]) -> str:
    """Render a spans list to plain text — used for search indexing."""
    out: list[str] = []
    for s in spans:
        if s["type"] in ("text", "code"):
            out.append(s.get("text", ""))
        elif s["type"] == "image":
            out.append(s.get("alt", ""))
    return "".join(out)


# Default singleton
_DEFAULT_PARSER = DocParser()


def parse(markdown: str, link_resolver: "LinkResolver | None" = None) -> list[dict[str, Any]]:
    return _DEFAULT_PARSER.parse(markdown, link_resolver=link_resolver)


def render_blocks_plain(blocks: list[dict[str, Any]]) -> str:
    """Render IR blocks back to plain text — used for search indexing."""
    out: list[str] = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            out.append(b["text"])
        elif t == "paragraph":
            out.append(b.get("text", render_spans_plain(b.get("spans", []))))
        elif t == "code":
            out.append(b["text"])
        elif t == "list":
            for item in b["items"]:
                out.append(render_blocks_plain(item))
        elif t == "blockquote":
            out.append(render_blocks_plain(b["blocks"]))
        elif t == "table":
            out.append(" ".join(b["headers"]))
            for row in b["rows"]:
                out.append(" ".join(row))
        elif t == "callout":
            out.append(render_blocks_plain(b["blocks"]))
    return "\n".join(s for s in out if s)
