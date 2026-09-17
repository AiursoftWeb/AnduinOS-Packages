"""Documentation loader.

Loads article metadata + markdown content from ``data/articles/`` and
builds an in-memory catalog. The catalog is populated lazily on first
access; subsequent calls return the cached instance.
"""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from anduinos_help.utils.logging import get_logger
from anduinos_help.utils.paths import articles_dir, index_path, meta_path
from anduinos_help.docs import parser as doc_parser

_log = get_logger("docs.loader")


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    category: str
    subcategory: str | None
    slug: str
    summary: str
    tags: tuple[str, ...]
    version: str               # "2.x", "1.x", "all"
    status: str                # current | legacy | deprecated
    source_url: str            # rendered docs URL
    source_repo_path: str      # path within AnduinOS-Docs repo
    source_repo_url: str       # GitHub blob URL
    raw_markdown_url: str      # raw.githubusercontent.com URL
    license: str
    license_url: str
    updated: str               # ISO date

    # Path to local markdown file (relative to articles dir)
    markdown_path: str = ""

    @property
    def display_category(self) -> str:
        return CATEGORY_DISPLAY.get(self.category, self.category.title())


@dataclass
class Catalog:
    articles: dict[str, Article] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    dataset_version: str = "0"
    dataset_fetched_at: str = ""
    source_url: str = "https://docs.anduinos.com/"

    def by_category(self, category: str) -> list[Article]:
        return sorted(
            (a for a in self.articles.values() if a.category == category),
            key=lambda a: a.title.lower(),
        )

    def subcategories(self, category: str) -> list[str]:
        subs: set[str] = set()
        for a in self.articles.values():
            if a.category == category and a.subcategory:
                subs.add(a.subcategory)
        return sorted(subs)

    def by_subcategory(self, category: str, subcategory: str) -> list[Article]:
        return sorted(
            (a for a in self.articles.values()
             if a.category == category and a.subcategory == subcategory),
            key=lambda a: a.title.lower(),
        )

    def get(self, article_id: str) -> Article | None:
        return self.articles.get(article_id)

    def related(self, article_id: str, limit: int = 5) -> list[Article]:
        a = self.articles.get(article_id)
        if not a:
            return []
        scored: list[tuple[float, Article]] = []
        for cand in self.articles.values():
            if cand.id == article_id:
                continue
            score = 0.0
            if cand.category == a.category:
                score += 2.0
            if cand.subcategory and a.subcategory and cand.subcategory == a.subcategory:
                score += 1.5
            shared_tags = set(cand.tags) & set(a.tags)
            score += 0.5 * len(shared_tags)
            if score > 0:
                scored.append((score, cand))
        scored.sort(key=lambda x: (-x[0], x[1].title.lower()))
        return [a for _, a in scored[:limit]]


# Category ordering for display. Categories not in this list are appended
# alphabetically.
CATEGORY_ORDER: list[str] = [
    "getting-started",
    "install",
    "applications",
    "system",
    "hardware",
    "networking",
    "terminal",
    "troubleshooting",
    "security",
    "developers",
    "virtualization",
    "servicing",
    "skills",
    "reference",
    "releases",
]

CATEGORY_DISPLAY: dict[str, str] = {
    "getting-started": "Getting Started",
    "install": "Installation",
    "applications": "Applications",
    "system": "System",
    "hardware": "Hardware",
    "networking": "Networking",
    "terminal": "Terminal",
    "troubleshooting": "Troubleshooting",
    "security": "Security",
    "developers": "Developers",
    "virtualization": "Virtualization",
    "servicing": "Server Services",
    "skills": "Advanced Skills",
    "reference": "Reference",
    "releases": "Release Notes",
}


_catalog: Catalog | None = None


def load_catalog(force: bool = False) -> Catalog:
    """Load the documentation catalog from disk.

    The first call reads ``index.json`` and every article's metadata file.
    Subsequent calls return the cached catalog unless ``force=True``.
    """
    global _catalog
    if _catalog and not force:
        return _catalog

    cat = Catalog()
    idx_file = index_path()
    if not idx_file.exists():
        _log.warning("Index file not found: %s", idx_file)
        _catalog = cat
        return cat

    try:
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.error("Failed to read index: %s", exc)
        _catalog = cat
        return cat

    cat.dataset_version = str(idx.get("dataset_version", "0"))
    cat.dataset_fetched_at = idx.get("fetched_at", "")
    cat.source_url = idx.get("source_url", "https://docs.anduinos.com/")
    cat.categories = list(idx.get("categories", []))

    for entry in idx.get("articles", []):
        try:
            art = Article(
                id=entry["id"],
                title=entry["title"],
                category=entry["category"],
                subcategory=entry.get("subcategory"),
                slug=entry["slug"],
                summary=entry.get("summary", ""),
                tags=tuple(entry.get("tags", [])),
                version=entry.get("version", "2.x"),
                status=entry.get("status", "current"),
                source_url=entry.get("source_url", ""),
                source_repo_path=entry.get("source_repo_path", ""),
                source_repo_url=entry.get("source_repo_url", ""),
                raw_markdown_url=entry.get("raw_markdown_url", ""),
                license=entry.get("license", "GPL-3.0"),
                license_url=entry.get("license_url", "https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/LICENSE"),
                updated=entry.get("updated", ""),
                markdown_path=entry.get("markdown_path", ""),
            )
            cat.articles[art.id] = art
        except KeyError as exc:
            _log.warning("Skipping malformed article entry (missing %s): %s", exc, entry.get("id"))
            continue
    _log.info("Loaded %d articles from %s", len(cat.articles), idx_file)
    _catalog = cat
    return cat


def load_markdown(article: Article) -> str:
    """Load the raw markdown content for an article."""
    if not article.markdown_path:
        return ""
    p = articles_dir() / article.markdown_path
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        _log.error("Failed to read markdown for %s: %s", article.id, exc)
        return ""


def load_article_blocks(article: Article) -> list[dict]:
    """Load + parse article markdown into IR blocks.

    Relative ``.md`` / ``.html`` links to sibling articles are resolved
    to ``anduinos-help://article/<id>`` URIs at parse time so the article
    view can navigate in-app when the user clicks them.
    """
    md = load_markdown(article)
    if not md:
        return []
    try:
        resolver = ArticleLinkResolver(article, load_catalog())
        return doc_parser.parse(md, link_resolver=resolver)
    except Exception as exc:  # noqa: BLE001
        _log.error("Failed to parse article %s: %s", article.id, exc)
        return []


class ArticleLinkResolver:
    """Resolves relative ``.md`` / ``.html`` hrefs to article IDs.

    Uses the current article's ``source_repo_path`` to resolve relative
    references against the bundled catalog. For example, given the
    current article is ``Docs/Apkg/Introduction.md`` and the href is
    ``Build-Your-First-Package.md``, the resolver returns
    ``developers/apkg-build-your-first-package``.
    """

    def __init__(self, article: "Article", catalog: "Catalog") -> None:
        self._article = article
        self._catalog = catalog
        # Build a lookup table of source_repo_path → article id, including
        # normalised variants (without leading "Docs/").
        self._by_src: dict[str, str] = {}
        for aid, art in catalog.articles.items():
            sp = art.source_repo_path
            self._by_src[sp] = aid
            if sp.startswith("Docs/"):
                self._by_src[sp[5:]] = aid
        # Also index by slug for short-name lookups
        self._by_slug: dict[str, str] = {
            art.slug: aid for aid, art in catalog.articles.items()
        }

    def resolve(self, href: str) -> str | None:
        if not href:
            return None
        # Strip anchor and query
        target = href.split("#", 1)[0].split("?", 1)[0]
        if not target:
            return None
        # Only resolve relative .md / .html links
        if target.startswith(("http://", "https://", "mailto:", "anduinos-help:")):
            return None
        if not target.endswith((".md", ".html")):
            return None
        stem = target.rsplit(".", 1)[0]
        if target.startswith("./"):
            stem = stem[2:]

        # Resolve relative to the current article's source directory
        src_path = self._article.source_repo_path
        parts = src_path.split("/")
        if parts and parts[0] == "Docs":
            parts = parts[1:]
        base_dir = "/".join(parts[:-1])

        # Try a few candidate paths
        candidates = [
            f"Docs/{base_dir}/{stem}.md" if base_dir else f"Docs/{stem}.md",
            f"{base_dir}/{stem}.md" if base_dir else f"{stem}.md",
            f"Docs/{stem}.md",
            f"{stem}.md",
        ]
        for cand in candidates:
            cand_norm = posixpath.normpath(cand)
            if cand in self._by_src:
                return self._by_src[cand]
            if cand_norm in self._by_src:
                return self._by_src[cand_norm]

        # Fall back to slug lookup (some links are short names)
        if stem in self._by_slug:
            return self._by_slug[stem]

        return None


def meta() -> dict:
    """Return the dataset meta.json contents (or empty dict)."""
    p = meta_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def categories_sorted() -> list[str]:
    """Return categories in display order."""
    cat = load_catalog()
    present = set(cat.categories)
    ordered = [c for c in CATEGORY_ORDER if c in present]
    extras = sorted(c for c in present if c not in CATEGORY_ORDER)
    return ordered + extras
