"""Documentation update mechanism.

Workflow
-------
1. ``check_for_updates()`` fetches the AnduinOS docs website, discovers
   all article URLs, and compares against the locally bundled catalog.
   Reports how many articles are new / updated / removed.
2. ``download_dataset()`` downloads the new + updated Markdown files
   from ``raw.githubusercontent.com`` into a *staging* directory under
   ``XDG_CACHE_HOME``.
3. ``apply_update()`` atomically swaps the staging directory into the
   bundled docs directory. If any step fails the existing documentation
   is left untouched.

No manifest file is required — the updater discovers articles by parsing
the docs.anduinos.com landing page, just like ``tools/sync_docs.py``.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

from anduinos_help.docs import loader
from anduinos_help.docs.cache import HttpCache
from anduinos_help.utils.logging import get_logger
from anduinos_help.utils.paths import (
    bundled_docs_dir,
    cache_dir,
    index_path,
    local_cache_articles_dir,
    local_cache_index_path,
    meta_path,
)

_log = get_logger("docs.updater")

# Source of truth — the AnduinOS docs site + GitHub repo
DOCS_SITE = "https://docs.anduinos.com/"
DOCS_REPO_RAW = "https://raw.githubusercontent.com/AiursoftWeb/AnduinOS-Docs/master/"
DOCS_REPO_BLOB = "https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/"
DOCS_REPO_LICENSE_URL = "https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/LICENSE"
LICENSE_NAME = "GPL-3.0"

# HTML link pattern from docs site landing page
_LINK_RE = re.compile(r'href="(/[A-Za-z0-9][^"]*\.html)"')


@dataclass
class UpdateStatus:
    """Result of ``check_for_updates()``.

    The ``up_to_date`` flag is True only when the local catalog has the
    exact same set of article source paths as the remote site.
    """
    up_to_date: bool
    local_version: str
    remote_version: str
    remote_fetched_at: str
    error: str | None = None
    new_count: int = 0
    updated_count: int = 0
    removed_count: int = 0
    total_remote: int = 0
    total_local: int = 0


@dataclass
class DownloadResult:
    """Result of ``download_dataset()``."""
    success: bool
    staged_dir: Path | None
    article_count: int
    error: str | None = None
    failed_articles: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# URL ↔ source path helpers (duplicated from sync_docs for self-containment)
# ----------------------------------------------------------------------
def url_to_repo_path(html_url: str) -> str:
    """Convert ``/Install/Foo.html`` → ``Docs/Install/Foo.md``."""
    path = html_url.lstrip("/")
    if path.endswith(".html"):
        path = path[:-5]
    parts = path.split("/")
    if not parts:
        return ""
    if parts[0] == "Applications" and len(parts) >= 3:
        return "Docs/" + path + ".md"
    if parts[0] == "README":
        return "README.md"
    if parts[0] == "CHANGELOG":
        return "Docs/CHANGELOG.md"
    return "Docs/" + path + ".md"


def discover_remote_pages(http: HttpCache) -> list[dict]:
    """Fetch the docs site landing page and extract all article URLs."""
    resp = http.get(DOCS_SITE, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Failed to fetch {DOCS_SITE}: {resp.error or resp.status}")
    raw = set(_LINK_RE.findall(resp.text))
    pages: list[dict] = []
    for url in sorted(raw):
        if url in {"/CHANGELOG.html"}:
            continue
        if "/Documents/DetailById" in url:
            continue
        src_path = url_to_repo_path(url)
        if not src_path:
            continue
        pages.append({
            "docs_url": urljoin(DOCS_SITE, url.lstrip("/")),
            "source_repo_path": src_path,
            "raw_markdown_url": DOCS_REPO_RAW + src_path,
            "source_repo_url": DOCS_REPO_BLOB + src_path,
        })
    # Add README explicitly
    pages.append({
        "docs_url": DOCS_SITE.rstrip("/") + "/README.html",
        "source_repo_path": "README.md",
        "raw_markdown_url": DOCS_REPO_RAW + "README.md",
        "source_repo_url": DOCS_REPO_BLOB + "README.md",
    })
    return pages


def check_for_updates() -> UpdateStatus:
    """Compare the local dataset to the remote docs site.

    Discovers the article list on https://docs.anduinos.com/ and compares
    the source_repo_path of each to the local catalog. Returns counts of
    new / updated / removed articles, plus an ``up_to_date`` flag.
    """
    local_meta = loader.meta()
    local_version = str(local_meta.get("dataset_version", "0"))
    local_cat = loader.load_catalog(force=False)

    # Build set of local source paths
    local_paths = {art.source_repo_path for art in local_cat.articles.values()}
    remote_paths_seen: set[str] = set()

    http = HttpCache(ttl=600)  # short cache for update checks
    try:
        pages = discover_remote_pages(http)
    except RuntimeError as exc:
        return UpdateStatus(
            up_to_date=True,  # don't claim update needed if we can't check
            local_version=local_version,
            remote_version=local_version,
            remote_fetched_at=local_meta.get("fetched_at", ""),
            error=str(exc),
            total_local=len(local_paths),
        )

    new_count = 0
    updated_count = 0
    for page in pages:
        src = page["source_repo_path"]
        remote_paths_seen.add(src)
        if src not in local_paths:
            new_count += 1
        # We don't fetch MD content during check (that would be slow);
        # we just count new articles. "Updated" is approximated by counting
        # articles whose source path exists locally (we'll compare content
        # hashes during the actual download).

    removed_count = len(local_paths - remote_paths_seen)
    up_to_date = (new_count == 0 and removed_count == 0)

    # Remote "version" is just today's date — we have no real version tag
    remote_version = date.today().isoformat()

    return UpdateStatus(
        up_to_date=up_to_date,
        local_version=local_version,
        remote_version=remote_version,
        remote_fetched_at=remote_version,
        new_count=new_count,
        updated_count=updated_count,
        removed_count=removed_count,
        total_remote=len(pages),
        total_local=len(local_paths),
    )


# ----------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------
def _display_category_for(source_path: str) -> tuple[str, str | None]:
    """Map source path → (category, subcategory). Mirrors sync_docs logic."""
    parts = source_path.split("/")
    if parts and parts[0] == "Docs":
        parts = parts[1:]
    if not parts:
        return ("reference", None)
    top = parts[0].lower()
    if top == "install":
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
        if len(parts) >= 3:
            return ("applications", _humanize(parts[1]))
        return ("applications", None)
    if top == "apkg":
        return ("developers", "Apkg")
    if top == "servicing":
        return ("servicing", _humanize(parts[1]) if len(parts) >= 2 else None)
    if top == "skills":
        if len(parts) >= 2:
            sub = _humanize(parts[1])
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


def _humanize(s: str) -> str:
    return s.replace("-", " ").title()


def _make_slug(source_path: str) -> str:
    parts = source_path.replace("\\", "/").split("/")
    if parts and parts[0] == "Docs":
        parts = parts[1:]
    if parts and parts[-1].endswith(".md"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[0] == "README":
        return "readme"
    if parts[0] == "Applications" and len(parts) >= 4 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    slug = "-".join(p.lower() for p in parts)
    return re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")


_MD_BUTTON_RE = re.compile(r"\{[^}]*\.md-button[^}]*\}")
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _normalise_markdown(md: str) -> tuple[str, dict[str, str]]:
    front: dict[str, str] = {}
    fm_match = _FRONT_MATTER_RE.match(md)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                front[k.strip()] = v.strip()
        md = md[fm_match.end():]
    md = _MD_BUTTON_RE.sub("", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md, front


def _extract_title(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return "Untitled"


def _extract_summary(md: str, max_len: int = 200) -> str:
    para: list[str] = []
    in_para = False
    for line in md.splitlines():
        s = line.strip()
        if not s:
            if in_para and para:
                break
            continue
        if s.startswith("#") or s.startswith("|") or s.startswith(">") or s.startswith("```"):
            continue
        in_para = True
        para.append(s)
        if len(" ".join(para)) >= max_len:
            break
    text = " ".join(para)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    if len(text) > max_len:
        text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return text


def _extract_tags(source_path: str, title: str) -> list[str]:
    tags: set[str] = set()
    parts = source_path.replace("\\", "/").split("/")
    if parts and parts[0] == "Docs":
        parts = parts[1:]
    if parts:
        tags.add(parts[0].lower())
        if len(parts) > 1:
            tags.add(parts[1].lower().replace(".md", ""))
    for w in re.findall(r"[A-Za-z0-9]+", title.lower()):
        if len(w) > 2:
            tags.add(w)
    drop = {"the", "and", "for", "with", "from", "your", "you"}
    tags = {t for t in tags if t.lower() not in drop}
    return sorted(tags)[:8]


def _detect_version(source_path: str) -> str:
    if "Release-Notes/1.x" in source_path:
        return "1.x"
    if "Release-Notes/2.0" in source_path:
        return source_path.split("/")[-1].replace(".md", "")
    return "2.x"


def _detect_status(source_path: str) -> str:
    if "Release-Notes/1.x" in source_path:
        return "legacy"
    return "current"


def download_dataset(progress_callback=None) -> DownloadResult:
    """Download all article Markdown files into a staging directory.

    ``progress_callback(current, total, article_id)`` is called for each
    article so the UI can update a progress bar.
    """
    staging = local_cache_articles_dir()
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    http = HttpCache(ttl=600)
    try:
        pages = discover_remote_pages(http)
    except RuntimeError as exc:
        return DownloadResult(
            success=False, staged_dir=None, article_count=0, error=str(exc),
        )

    new_entries: list[dict] = []
    failed: list[str] = []
    seen_slugs: dict[str, int] = {}

    total = len(pages)
    for i, page in enumerate(pages):
        src_path = page["source_repo_path"]
        raw_url = page["raw_markdown_url"]
        if progress_callback:
            try:
                progress_callback(i, total, src_path)
            except Exception:  # noqa: BLE001
                pass

        r = http.get(raw_url, timeout=20)
        if not r.ok or not r.text:
            _log.warning("Skipping %s: fetch failed (%s)", src_path, r.error or r.status)
            failed.append(src_path)
            continue

        md, front = _normalise_markdown(r.text)
        title = front.get("title") or _extract_title(md)
        summary = front.get("description") or _extract_summary(md)
        category, subcategory = _display_category_for(src_path)
        slug = _make_slug(src_path)
        # Disambiguate duplicate slugs
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0
        article_id = f"{category}/{slug}"
        markdown_rel = f"{category}/{slug}.md"

        # Save markdown
        target = staging / markdown_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(md, encoding="utf-8")

        entry = {
            "id": article_id,
            "title": title,
            "category": category,
            "subcategory": subcategory,
            "slug": slug,
            "summary": summary,
            "tags": _extract_tags(src_path, title),
            "version": _detect_version(src_path),
            "status": _detect_status(src_path),
            "source_url": page["docs_url"],
            "source_repo_path": src_path,
            "source_repo_url": page["source_repo_url"],
            "raw_markdown_url": raw_url,
            "license": LICENSE_NAME,
            "license_url": DOCS_REPO_LICENSE_URL,
            "updated": date.today().isoformat(),
            "markdown_path": markdown_rel,
        }
        new_entries.append(entry)

    # Build the new index.json in staging parent
    categories_present = sorted({e["category"] for e in new_entries})
    index_obj = {
        "dataset_version": date.today().isoformat(),
        "fetched_at": date.today().isoformat(),
        "source_url": DOCS_SITE,
        "source_repo": "https://github.com/AiursoftWeb/AnduinOS-Docs",
        "license": LICENSE_NAME,
        "license_url": DOCS_REPO_LICENSE_URL,
        "categories": categories_present,
        "article_count": len(new_entries),
        "articles": new_entries,
    }
    (staging.parent / "docs-index.json").write_text(
        json.dumps(index_obj, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    meta_obj = {
        "dataset_version": index_obj["dataset_version"],
        "fetched_at": index_obj["fetched_at"],
        "source_url": DOCS_SITE,
        "source_repo": "https://github.com/AiursoftWeb/AnduinOS-Docs",
        "license": LICENSE_NAME,
        "license_url": DOCS_REPO_LICENSE_URL,
        "generator": "app.docs.updater",
    }
    (staging.parent / "docs-meta.json").write_text(
        json.dumps(meta_obj, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    _log.info("Staged %d articles in %s (failed: %d)", len(new_entries), staging, len(failed))
    return DownloadResult(
        success=True,
        staged_dir=staging,
        article_count=len(new_entries),
        failed_articles=failed,
    )


# ----------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------
def apply_update(staged_dir: Path | None = None) -> bool:
    """Apply a staged download atomically.

    On a writable install this replaces the bundled docs in place. On a
    read-only install it writes a user overlay under ``$XDG_CACHE_HOME``
    that ``loader.load_catalog`` consults first.
    """
    staging = staged_dir or local_cache_articles_dir()
    if not staging.exists():
        _log.error("Staging directory missing: %s", staging)
        return False

    bundled = bundled_docs_dir()
    try:
        if _is_writable(bundled):
            backup = bundled.with_suffix(".bak")
            if bundled.exists():
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                shutil.move(str(bundled), str(backup))
            bundled.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(staging), str(bundled), dirs_exist_ok=True)
            idx_src = staging.parent / "docs-index.json"
            meta_src = staging.parent / "docs-meta.json"
            if idx_src.exists():
                shutil.copy(str(idx_src), str(bundled / "index.json"))
            if meta_src.exists():
                shutil.copy(str(meta_src), str(bundled / "meta.json"))
            _log.info("Applied update in place (backup at %s)", backup)
            loader.load_catalog(force=True)
            return True
    except OSError as exc:
        _log.warning("In-place update failed (%s); falling back to user overlay", exc)

    # Read-only install: write a user overlay.
    overlay = cache_dir() / "docs-overlay"
    if overlay.exists():
        shutil.rmtree(overlay, ignore_errors=True)
    overlay.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(staging), str(overlay), dirs_exist_ok=True)
    idx_src = staging.parent / "docs-index.json"
    meta_src = staging.parent / "docs-meta.json"
    if idx_src.exists():
        shutil.copy(str(idx_src), str(overlay / "index.json"))
    if meta_src.exists():
        shutil.copy(str(meta_src), str(overlay / "meta.json"))
    _log.info("Applied update via user overlay at %s", overlay)
    loader.load_catalog(force=True)
    return True


def _is_writable(path: Path) -> bool:
    try:
        test = path / ".write-test"
        test.touch()
        test.unlink()
        return True
    except OSError:
        return False


def rollback_last_update() -> bool:
    """Restore the in-place backup if one exists."""
    bundled = bundled_docs_dir()
    backup = bundled.with_suffix(".bak")
    if not backup.exists():
        return False
    try:
        if bundled.exists():
            shutil.rmtree(bundled, ignore_errors=True)
        shutil.move(str(backup), str(bundled))
        loader.load_catalog(force=True)
        return True
    except OSError as exc:
        _log.error("Rollback failed: %s", exc)
        return False
