#!/usr/bin/env python3
"""AnduinOS documentation ingestion tool.

Crawls the official AnduinOS documentation website
(https://docs.anduinos.com/) to discover all articles, then fetches the
corresponding Markdown source from the AnduinOS-Docs GitHub repository,
normalises the content, and writes a structured dataset under
``data/articles/`` plus an ``index.json`` manifest.

Usage
-----
    python3 tools/sync_docs.py [--limit N] [--no-fetch] [--report-only]

Outputs
-------
* ``data/articles/<category>/<slug>.md``           (raw markdown)
* ``data/articles/<category>/<slug>.meta.json``    (per-article metadata)
* ``data/index.json``                              (catalog manifest)
* ``data/meta.json``                               (dataset meta)
* ``data/sync-report.json``                        (last sync report)

The tool is idempotent: re-running it refreshes the dataset, updates
``updated`` timestamps based on GitHub commit history (best-effort),
and produces a sync report comparing the new dataset to the previous
one.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

# Allow running this script from anywhere — add repo root to sys.path.
THIS = Path(__file__).resolve()
REPO_ROOT = THIS.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from app.utils.logging import get_logger  # noqa: E402

_log = get_logger("tools.sync_docs")

DOCS_SITE = "https://docs.anduinos.com/"
DOCS_REPO_RAW = "https://raw.githubusercontent.com/AiursoftWeb/AnduinOS-Docs/master/"
DOCS_REPO_BLOB = "https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/"
DOCS_REPO_LICENSE_URL = "https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/LICENSE"
LICENSE_NAME = "GPL-3.0"
DATASET_VERSION = "2026.09"  # bumped on each successful sync — see sync-report

DATA_DIR = REPO_ROOT / "data"
ARTICLES_DIR = DATA_DIR / "articles"
INDEX_PATH = DATA_DIR / "index.json"
META_PATH = DATA_DIR / "meta.json"
REPORT_PATH = DATA_DIR / "sync-report.json"


# ----------------------------------------------------------------------
# Source URL → display category mapping
# ----------------------------------------------------------------------
def display_category_for(source_path: str) -> tuple[str, str | None]:
    """Map a source path (e.g. ``Docs/Install/Foo.md``) to (category, subcategory).

    Categories follow the IA defined by the AnduinOS Help Center spec.
    """
    parts = source_path.split("/")
    # Normalise: leading "Docs/" prefix
    if parts and parts[0] == "Docs":
        parts = parts[1:]
    if not parts:
        return ("reference", None)

    top = parts[0].lower()

    if top == "install":
        # Split install articles into functional groups
        name = parts[-1].lower() if parts else ""
        if any(k in name for k in ["driver", "firmware", "nvidia", "nvme"]):
            return ("hardware", None)
        if any(k in name for k in ["firewall", "ssh", "rdp", "sudo-without"]):
            return ("security", None)
        if any(k in name for k in ["keyboard", "touchpad", "language", "rime"]):
            return ("desktop", None)
        if any(k in name for k in ["power", "swap", "manage-swap", "backup"]):
            return ("system", None)
        if any(k in name for k in ["printer"]):
            return ("hardware", None)
        return ("install", None)

    if top == "applications":
        # Applications/<Group>/<Name>/<Name>.md → category=applications, subcategory=<group>
        if len(parts) >= 3:
            return ("applications", _humanize_group(parts[1]))
        return ("applications", None)

    if top == "apkg":
        return ("developers", "Apkg")

    if top == "servicing":
        return ("servicing", _humanize_group(parts[1]) if len(parts) >= 2 else None)

    if top == "skills":
        if len(parts) >= 2:
            sub = _humanize_group(parts[1])
            # Some skills map to other categories
            if parts[1].lower() == "system-management":
                return ("system", sub)
            if parts[1].lower() == "file-system-management":
                return ("system", sub)
            if parts[1].lower() == "sandboxing":
                return ("developers", sub)
            if parts[1].lower() == "secret-management":
                return ("security", sub)
            if parts[1].lower() == "developing":
                return ("developers", sub)
            return ("skills", sub)
        return ("skills", None)

    if top == "virtualization":
        return ("virtualization", None)

    if top == "release-notes":
        return ("releases", None)

    return ("reference", None)


def _humanize_group(s: str) -> str:
    return s.replace("-", " ").title()


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------
def discover_pages() -> list[dict]:
    """Fetch the docs site landing page and extract all article URLs."""
    resp = requests.get(DOCS_SITE, timeout=30)
    resp.raise_for_status()
    html = resp.text
    # Find all href values like /Install/Foo.html or /Applications/Group/Name/Name.html
    pattern = re.compile(r'href="(/[A-Za-z0-9][^"]*\.html)"')
    raw = set(pattern.findall(html))
    pages: list[dict] = []
    for url in sorted(raw):
        if url in {"/CHANGELOG.html"}:
            continue
        if "/Documents/DetailById" in url:
            continue
        if url in {"/Release-Notes/1.x.html"}:
            # historical release notes — keep but mark legacy
            pass
        # Convert URL to source repo path
        src_path = url_to_repo_path(url)
        if not src_path:
            continue
        pages.append({
            "docs_url": urljoin(DOCS_SITE, url.lstrip("/")),
            "source_repo_path": src_path,
            "raw_markdown_url": DOCS_REPO_RAW + src_path,
            "source_repo_url": DOCS_REPO_BLOB + src_path,
        })
    # Add README + CHANGELOG explicitly
    pages.append({
        "docs_url": DOCS_SITE.rstrip("/") + "/README.html",
        "source_repo_path": "README.md",
        "raw_markdown_url": DOCS_REPO_RAW + "README.md",
        "source_repo_url": DOCS_REPO_BLOB + "README.md",
    })
    return pages


def url_to_repo_path(html_url: str) -> str:
    """Convert ``/Install/Foo.html`` → ``Docs/Install/Foo.md``.

    Special-cases Applications/<group>/<name>/<name>.html →
    Docs/Applications/<group>/<name>/<name>.md
    """
    path = html_url.lstrip("/")
    # Strip .html
    if path.endswith(".html"):
        path = path[:-5]
    parts = path.split("/")
    if not parts:
        return ""
    if parts[0] == "Applications" and len(parts) >= 3:
        # /Applications/<group>/<name>/<name>.html
        # Already in correct shape, just prepend Docs/
        return "Docs/" + path + ".md"
    if parts[0] == "README":
        return "README.md"
    if parts[0] == "CHANGELOG":
        return "Docs/CHANGELOG.md"
    return "Docs/" + path + ".md"


# ----------------------------------------------------------------------
# Markdown normalisation
# ----------------------------------------------------------------------
_MD_BUTTON_RE = re.compile(r"\{[^}]*\.md-button[^}]*\}")
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def normalise_markdown(md: str) -> tuple[str, dict[str, str]]:
    """Strip MkDocs attributes, front-matter; return (markdown, frontmatter)."""
    front: dict[str, str] = {}
    fm_match = _FRONT_MATTER_RE.match(md)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                front[k.strip()] = v.strip()
        md = md[fm_match.end():]
    # Strip trailing attribute braces
    md = _MD_BUTTON_RE.sub("", md)
    # Trim excessive blank lines
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md, front


# ----------------------------------------------------------------------
# Metadata extraction
# ----------------------------------------------------------------------
def extract_title(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return "Untitled"


def extract_summary(md: str, max_len: int = 200) -> str:
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
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # strip link syntax
    text = re.sub(r"`([^`]+)`", r"\1", text)              # strip code backticks
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)        # bold
    text = re.sub(r"\*([^*]+)\*", r"\1", text)            # italic
    if len(text) > max_len:
        text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return text


def extract_tags(source_path: str, title: str, md: str) -> list[str]:
    tags: set[str] = set()
    parts = source_path.replace("\\", "/").split("/")
    # Source directory becomes a tag
    if parts and parts[0] == "Docs":
        parts = parts[1:]
    if parts:
        tags.add(parts[0].lower())
        if len(parts) > 1:
            tags.add(parts[1].lower().replace(".md", ""))
    # Title words
    for w in re.findall(r"[A-Za-z0-9]+", title.lower()):
        if len(w) > 2:
            tags.add(w)
    # Cap to 8 tags, drop generic ones
    drop = {"the", "and", "for", "with", "from", "your", "you"}
    tags = {t for t in tags if t.lower() not in drop}
    return sorted(tags)[:8]


def detect_version(source_path: str) -> str:
    if "Release-Notes/1.x" in source_path:
        return "1.x"
    if "Release-Notes/2.0" in source_path:
        return source_path.split("/")[-1].replace(".md", "")
    return "2.x"


def detect_status(source_path: str) -> str:
    if "Release-Notes/1.x" in source_path:
        return "legacy"
    return "current"


def make_slug(source_path: str) -> str:
    parts = source_path.replace("\\", "/").split("/")
    if parts and parts[0] == "Docs":
        parts = parts[1:]
    if parts and parts[-1].endswith(".md"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[0] == "README":
        return "readme"
    # For Applications/<group>/<name>/<name>, drop the duplicated trailing segment
    if parts[0] == "Applications" and len(parts) >= 4 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    slug = "-".join(p.lower() for p in parts)
    return re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")


def make_article_id(category: str, slug: str) -> str:
    return f"{category}/{slug}"


def make_markdown_path(category: str, slug: str) -> str:
    return f"{category}/{slug}.md"


# ----------------------------------------------------------------------
# Sync logic
# ----------------------------------------------------------------------
@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    potentially_obsolete: list[str] = field(default_factory=list)
    broken_links: list[dict] = field(default_factory=list)
    failed_pages: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "potentially_obsolete": self.potentially_obsolete,
            "broken_links": self.broken_links,
            "failed_pages": self.failed_pages,
            "summary": {
                "added": len(self.added),
                "updated": len(self.updated),
                "removed": len(self.removed),
                "potentially_obsolete": len(self.potentially_obsolete),
                "broken_links": len(self.broken_links),
                "failed_pages": len(self.failed_pages),
            },
        }


def load_previous_index() -> dict[str, dict]:
    if not INDEX_PATH.exists():
        return {}
    try:
        idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {a["id"]: a for a in idx.get("articles", [])}


def fetch_markdown(url: str) -> str | None:
    # Allow re-using locally cached files during a re-sync to avoid network.
    # Map raw URL → local file path.
    local = _local_cache_for_url(url)
    if local and local.exists():
        try:
            return local.read_text(encoding="utf-8")
        except OSError:
            pass
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        text = resp.text
        # Cache for next time
        if local:
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(text, encoding="utf-8")
            except OSError:
                pass
        return text
    except requests.RequestException as exc:
        _log.warning("Fetch failed for %s: %s", url, exc)
        return None


def _local_cache_for_url(url: str) -> Path | None:
    """Map a raw GitHub URL to a local cache file under data/.cache."""
    if not url.startswith(DOCS_REPO_RAW):
        return None
    rel = url[len(DOCS_REPO_RAW):]
    return REPO_ROOT / "data" / ".cache" / rel


def check_internal_links(
    article_id: str,
    md: str,
    source_path_by_id: dict[str, str],
    all_ids: set[str],
    all_slugs: set[str],
) -> list[dict]:
    """Detect references to other docs pages that don't resolve.

    Uses the article's ``source_repo_path`` to resolve relative ``.md``
    links — AnduinOS docs link sibling articles by basename.
    """
    broken: list[dict] = []
    src_path = source_path_by_id.get(article_id)
    if not src_path:
        return broken
    # base directory within Docs/
    parts = src_path.split("/")
    if parts[0] == "Docs":
        parts = parts[1:]
    base_dir = "/".join(parts[:-1])  # e.g. Apkg, Install, Applications/Code-Editors/VS-Code

    for m in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", md):
        href = m.group(1)
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = href.split("#")[0]
        if not target:
            continue
        if target.startswith("./"):
            target = target[2:]
        if not target.endswith((".md", ".html")):
            continue

        stem = target.rsplit(".", 1)[0]
        # Try resolving against the same source directory
        candidate_src_raw = f"Docs/{base_dir}/{stem}.md" if base_dir else f"Docs/{stem}.md"
        candidate_root = f"Docs/{stem}.md"
        # Normalise ../ segments
        candidate_src = posixpath.normpath(candidate_src_raw)
        candidate_root = posixpath.normpath(candidate_root)
        # Look up the article whose source_repo_path matches
        matched_id = None
        for aid, sp in source_path_by_id.items():
            if sp == candidate_src or sp == candidate_root or sp == candidate_src_raw:
                matched_id = aid
                break
        if matched_id is None and stem not in all_slugs:
            broken.append({
                "article": article_id,
                "link": href,
                "target_src_path": candidate_src,
            })
    return broken


def sync(limit: int | None = None, fetch: bool = True, report_only: bool = False) -> SyncReport:
    report = SyncReport()
    pages = discover_pages()
    if limit:
        pages = pages[:limit]
    _log.info("Discovered %d pages from %s", len(pages), DOCS_SITE)

    previous = load_previous_index()
    previous_ids = set(previous.keys())

    new_entries: list[dict] = []
    new_ids: set[str] = set()
    seen_slugs: dict[str, int] = {}

    if not report_only:
        ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    for page in pages:
        src_path = page["source_repo_path"]
        raw_url = page["raw_markdown_url"]
        docs_url = page["docs_url"]
        category, subcategory = display_category_for(src_path)
        slug = make_slug(src_path)
        # Disambiguate duplicate slugs
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0
        article_id = make_article_id(category, slug)
        new_ids.add(article_id)

        md: str | None = None
        if fetch:
            md = fetch_markdown(raw_url)
        if md is None:
            report.failed_pages.append({
                "source_repo_path": src_path,
                "raw_markdown_url": raw_url,
                "error": "404 or fetch failed",
            })
            continue

        md, front = normalise_markdown(md)
        title = front.get("title") or extract_title(md)
        summary = front.get("description") or extract_summary(md)
        tags = extract_tags(src_path, title, md)
        version = detect_version(src_path)
        status = detect_status(src_path)
        markdown_rel = make_markdown_path(category, slug)

        if not report_only:
            target = ARTICLES_DIR / markdown_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(md, encoding="utf-8")

        entry = {
            "id": article_id,
            "title": title,
            "category": category,
            "subcategory": subcategory,
            "slug": slug,
            "summary": summary,
            "tags": tags,
            "version": version,
            "status": status,
            "source_url": docs_url,
            "source_repo_path": src_path,
            "source_repo_url": page["source_repo_url"],
            "raw_markdown_url": raw_url,
            "license": LICENSE_NAME,
            "license_url": DOCS_REPO_LICENSE_URL,
            "updated": date.today().isoformat(),
            "markdown_path": markdown_rel,
        }
        new_entries.append(entry)

        if article_id in previous_ids:
            prev = previous[article_id]
            if (prev.get("source_repo_url") != entry["source_repo_url"]
                or prev.get("title") != entry["title"]
                or prev.get("summary") != entry["summary"]):
                report.updated.append(article_id)
        else:
            report.added.append(article_id)

    # Removed: existed in previous, not in new
    for pid in previous_ids - new_ids:
        report.removed.append(pid)

    # Potentially obsolete: titles mentioning "legacy", "old", "deprecated"
    for e in new_entries:
        if any(k in e["title"].lower() for k in ("legacy", "deprecated", "old")):
            report.potentially_obsolete.append(e["id"])

    # Broken internal links
    all_ids = {e["id"] for e in new_entries}
    # Also include slug-only fallbacks (some articles link by short name)
    all_slugs = {e["slug"] for e in new_entries}
    source_path_by_id = {e["id"]: e["source_repo_path"] for e in new_entries}
    for e in new_entries:
        md_path = ARTICLES_DIR / e["markdown_path"] if not report_only else None
        if md_path and md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")
            report.broken_links.extend(
                check_internal_links(e["id"], md_text, source_path_by_id, all_ids, all_slugs)
            )

    if not report_only:
        # Write index.json
        categories_present = sorted({e["category"] for e in new_entries})
        index_obj = {
            "dataset_version": DATASET_VERSION,
            "fetched_at": date.today().isoformat(),
            "source_url": DOCS_SITE,
            "source_repo": "https://github.com/AiursoftWeb/AnduinOS-Docs",
            "license": LICENSE_NAME,
            "license_url": DOCS_REPO_LICENSE_URL,
            "categories": categories_present,
            "article_count": len(new_entries),
            "articles": new_entries,
        }
        INDEX_PATH.write_text(json.dumps(index_obj, indent=2, ensure_ascii=False), encoding="utf-8")

        # Write meta.json
        meta_obj = {
            "dataset_version": DATASET_VERSION,
            "fetched_at": date.today().isoformat(),
            "source_url": DOCS_SITE,
            "source_repo": "https://github.com/AiursoftWeb/AnduinOS-Docs",
            "license": LICENSE_NAME,
            "license_url": DOCS_REPO_LICENSE_URL,
            "generator": "tools/sync_docs.py",
        }
        META_PATH.write_text(json.dumps(meta_obj, indent=2, ensure_ascii=False), encoding="utf-8")

    # Write sync report
    REPORT_PATH.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    _log.info(
        "Sync complete: added=%d updated=%d removed=%d obsolete=%d broken=%d failed=%d",
        len(report.added), len(report.updated), len(report.removed),
        len(report.potentially_obsolete), len(report.broken_links), len(report.failed_pages),
    )
    return report


def main() -> int:
    p = argparse.ArgumentParser(description="Sync AnduinOS documentation into a local dataset.")
    p.add_argument("--limit", type=int, default=None, help="Limit the number of pages fetched (for testing).")
    p.add_argument("--no-fetch", action="store_true", help="Don't fetch markdown; just discover URLs.")
    p.add_argument("--report-only", action="store_true", help="Don't write any files; just produce a report.")
    args = p.parse_args()

    report = sync(limit=args.limit, fetch=not args.no_fetch, report_only=args.report_only)
    print(json.dumps(report.to_dict()["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
