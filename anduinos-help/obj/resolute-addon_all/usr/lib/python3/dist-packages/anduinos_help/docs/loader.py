"""Documentation loader — SQLite-backed catalog API.

Provides the same ``Article`` / ``Catalog`` dataclass surface the UI
used in the original flat-file version, but reads from the SQLite
store instead of ``assets/articles/`` + ``assets/index.json``. The
catalog is populated lazily on first access; subsequent calls return
the cached instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from anduinos_help.docs import parser as doc_parser, store
from anduinos_help.utils.logging import get_logger

_log = get_logger("docs.loader")


@dataclass(frozen=True)
class Article:
    """A single documentation article, as seen by the UI."""
    id: str                  # UI id (lowercased path without .md)
    title: str
    category: str
    subcategory: str | None
    slug: str
    summary: str
    tags: tuple[str, ...]
    version: str             # "2.x", "1.x", or specific like "2.0.3"
    status: str              # current | legacy | deprecated
    source_url: str
    source_repo_path: str    # repo-relative path, e.g. "Install/Foo.md"
    source_repo_url: str
    raw_markdown_url: str
    license: str
    license_url: str
    updated: str             # ISO date

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
        # Try exact match first
        if article_id in self.articles:
            return self.articles[article_id]
        # Case-insensitive fallback
        lower = article_id.lower()
        for aid, art in self.articles.items():
            if aid.lower() == lower:
                return art
        return None

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
    "Home",
    "Install",
    "Skills",
    "Applications",
    "Apkg",
    "Servicing",
    "Virtualization",
]

CATEGORY_DISPLAY: dict[str, str] = {
    "Home": "Home",
    "Install": "Installation",
    "Skills": "Skills",
    "Applications": "Applications",
    "Apkg": "Apkg Packaging",
    "Servicing": "Server Services",
    "Virtualization": "Virtualization",
    "root": "Miscellaneous",
}


_catalog: Catalog | None = None


def load_catalog(force: bool = False) -> Catalog:
    """Load the documentation catalog from the SQLite store.

    The first call queries the database and builds an in-memory catalog.
    Subsequent calls return the cached catalog unless ``force=True``.
    """
    global _catalog
    if _catalog and not force:
        return _catalog

    cat = Catalog()
    try:
        store.init_db()
    except Exception as exc:  # noqa: BLE001
        _log.error("Failed to init docs store: %s", exc)
        _catalog = cat
        return cat

    try:
        documents = store.list_documents()
    except Exception as exc:  # noqa: BLE001
        _log.error("Failed to list documents: %s", exc)
        _catalog = cat
        return cat

    # Build the nav tree so we can derive subcategories for each document.
    try:
        nav_entries = store.nav_tree()
    except Exception:  # noqa: BLE001
        nav_entries = []
    subcategory_map = _build_subcategory_map(nav_entries)

    seen_categories: set[str] = set()
    for doc in documents:
        article = Article(
            id=doc.article_id,
            title=doc.title,
            category=doc.category,
            subcategory=subcategory_map.get(doc.file_path),
            slug=doc.slug,
            summary=_extract_summary(doc.content),
            tags=_extract_tags(doc),
            version=_detect_version(doc.file_path),
            status=_detect_status(doc.file_path),
            source_url=doc.source_url,
            source_repo_path=doc.file_path,
            source_repo_url=doc.source_repo_url,
            raw_markdown_url="",
            license="GPL-3.0",
            license_url="https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/LICENSE",
            updated=doc.file_last_modified[:10] if doc.file_last_modified else "",
        )
        cat.articles[article.id] = article
        seen_categories.add(article.category)

    # Order categories per CATEGORY_ORDER, then alphabetical
    ordered = [c for c in CATEGORY_ORDER if c in seen_categories]
    extras = sorted(c for c in seen_categories if c not in CATEGORY_ORDER)
    cat.categories = ordered + extras

    _log.info("Loaded %d articles from SQLite store", len(cat.articles))
    _catalog = cat
    return cat


def load_markdown(article: Article) -> str:
    """Load the raw markdown content for an article from the SQLite store."""
    doc = store.get_document_by_path(article.source_repo_path)
    if not doc:
        return ""
    return doc.content


def load_article_blocks(article: Article) -> list[dict]:
    """Load + parse article markdown into IR blocks."""
    md = load_markdown(article)
    if not md:
        return []
    try:
        return doc_parser.parse(md)
    except Exception as exc:  # noqa: BLE001
        _log.error("Failed to parse article %s: %s", article.id, exc)
        return []


def categories_sorted() -> list[str]:
    """Return categories in display order."""
    return load_catalog().categories


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _build_subcategory_map(nav_entries: list) -> dict[str, str]:
    """Walk the nav tree and map file_path → top-level subgroup name.

    e.g. for the nav tree:
        - Install:
          - Installing AnduinOS:        # subgroup
            - System Requirements: Install/System-Requirements.md
    We map "Install/System-Requirements.md" → "Installing AnduinOS".
    """
    out: dict[str, str] = {}

    def _walk(node, parent_group: str | None) -> None:
        # If this node is a group (no path), it's a potential subcategory.
        if getattr(node, "is_group", False) or (node.path is None and not getattr(node, "is_external", False)):
            for child in getattr(node, "children", []):
                _walk(child, parent_group=node.title)
            return
        # Leaf node — if we have a parent_group, record it.
        if parent_group and node.path and not getattr(node, "is_external", False):
            p = node.path.lstrip("./").lstrip("/")
            out[p] = parent_group
        # Recurse into children even for leaves (shouldn't normally happen)
        for child in getattr(node, "children", []):
            _walk(child, parent_group)

    for n in nav_entries:
        _walk(n, parent_group=None)
    return out


def _extract_summary(md: str, max_len: int = 200) -> str:
    """Pull the first non-heading paragraph as a summary."""
    para: list[str] = []
    in_para = False
    for line in md.splitlines():
        s = line.strip()
        if not s:
            if in_para and para:
                break
            continue
        if s.startswith("#"):
            continue
        if s.startswith("|") or s.startswith(">") or s.startswith("```"):
            continue
        in_para = True
        para.append(s)
        if len(" ".join(para)) >= max_len:
            break
    text = " ".join(para)
    # Strip simple markdown formatting
    import re
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    if len(text) > max_len:
        text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return text


def _extract_tags(doc) -> tuple[str, ...]:
    """Derive tags from the document's category + title."""
    import re
    tags: set[str] = set()
    parts = doc.file_path.replace("\\", "/").split("/")
    if parts:
        tags.add(parts[0].lower())
        if len(parts) > 1:
            tags.add(parts[1].lower().replace(".md", ""))
    for w in re.findall(r"[A-Za-z0-9]+", doc.title.lower()):
        if len(w) > 2:
            tags.add(w)
    drop = {"the", "and", "for", "with", "from", "your", "you"}
    tags = {t for t in tags if t.lower() not in drop}
    return tuple(sorted(tags)[:8])


def _detect_version(source_path: str) -> str:
    if "Release-Notes/1.x" in source_path:
        return "1.x"
    if "Release-Notes/2.0" in source_path:
        # e.g. "Release-Notes/2.0.3.md" → "2.0.3"
        name = source_path.split("/")[-1].replace(".md", "")
        return name
    return "2.x"


def _detect_status(source_path: str) -> str:
    if "Release-Notes/1.x" in source_path:
        return "legacy"
    return "current"
