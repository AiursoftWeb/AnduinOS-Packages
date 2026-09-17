"""Tests for the AnduinOS Help documentation loader.

These tests verify behaviour — what the loader does when given real bundled
article data — rather than implementation details (file counts, exact
constant values, or .deb structure).

Run from the package directory:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src LANGUAGE=C \\
        python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

# Make the source tree discoverable when running tests in the CI pattern.
SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# When running from a source tree, point the data-dir resolver at assets/.
ASSETS = pathlib.Path(__file__).resolve().parents[1] / "assets"
if ASSETS.is_dir():
    os.environ.setdefault("ANDUINOS_HELP_DATA_DIR", str(ASSETS))

from anduinos_help.docs import loader  # noqa: E402


class LoaderBehaviourTests(unittest.TestCase):
    """Verify what the loader does, not how it's structured."""

    def test_load_catalog_returns_a_non_empty_catalog(self):
        cat = loader.load_catalog(force=True)
        self.assertIsInstance(cat.articles, dict)
        self.assertGreater(len(cat.articles), 0,
                           "Bundled dataset must contain at least one article")

    def test_every_article_carries_source_attribution(self):
        """Every article must preserve its original source URL so the
        documentation remains auditable per the AnduinOS Docs license."""
        cat = loader.load_catalog(force=True)
        missing = [
            aid for aid, art in cat.articles.items()
            if not art.source_url or not art.source_repo_path
        ]
        self.assertEqual(missing, [], "These articles lost their source attribution")

    def test_every_article_has_a_version_tag(self):
        """Articles without a version tag would silently mix current and
        legacy documentation — that's a safety boundary."""
        cat = loader.load_catalog(force=True)
        untagged = [aid for aid, art in cat.articles.items() if not art.version]
        self.assertEqual(untagged, [], "Articles without a version tag")

    def test_load_markdown_returns_a_non_empty_string_for_real_articles(self):
        cat = loader.load_catalog(force=True)
        art = next(iter(cat.articles.values()))
        md = loader.load_markdown(art)
        self.assertIsInstance(md, str)
        self.assertGreater(len(md), 100, "Markdown too short — looks empty")

    def test_load_markdown_for_missing_file_returns_empty_string(self):
        """Failing to find an article must not crash; it must return ""."""
        cat = loader.load_catalog(force=True)
        art = next(iter(cat.articles.values()))
        # Create an article that points at a non-existent path
        bad = loader.Article(
            id=art.id, title=art.title, category=art.category,
            subcategory=art.subcategory, slug=art.slug, summary=art.summary,
            tags=art.tags, version=art.version, status=art.status,
            source_url=art.source_url, source_repo_path=art.source_repo_path,
            source_repo_url=art.source_repo_url, raw_markdown_url=art.raw_markdown_url,
            license=art.license, license_url=art.license_url, updated=art.updated,
            markdown_path="definitely/does/not/exist.md",
        )
        self.assertEqual(loader.load_markdown(bad), "")

    def test_load_article_blocks_returns_at_least_one_heading(self):
        """Every real article must parse into blocks, and the first
        block should typically be a top-level heading."""
        cat = loader.load_catalog(force=True)
        # Sample a few articles to keep the test fast
        for art in list(cat.articles.values())[:5]:
            with self.subTest(article=art.id):
                blocks = loader.load_article_blocks(art)
                self.assertGreater(len(blocks), 0, f"{art.id} parsed to 0 blocks")
                self.assertEqual(blocks[0]["type"], "heading",
                                 f"{art.id} first block is {blocks[0]['type']}")

    def test_load_article_blocks_does_not_raise_on_malformed_markdown(self):
        """The parser must never crash the app — malformed input returns []."""
        # Build an article pointing at empty markdown
        cat = loader.load_catalog(force=True)
        art = next(iter(cat.articles.values()))
        bad = loader.Article(
            id=art.id, title=art.title, category=art.category,
            subcategory=art.subcategory, slug=art.slug, summary=art.summary,
            tags=art.tags, version=art.version, status=art.status,
            source_url=art.source_url, source_repo_path=art.source_repo_path,
            source_repo_url=art.source_repo_url, raw_markdown_url=art.raw_markdown_url,
            license=art.license, license_url=art.license_url, updated=art.updated,
            markdown_path="definitely/does/not/exist.md",
        )
        # Should return [] rather than raising
        blocks = loader.load_article_blocks(bad)
        self.assertEqual(blocks, [])

    def test_related_articles_excludes_self(self):
        """The related() helper must never return the same article as self."""
        cat = loader.load_catalog(force=True)
        for art in list(cat.articles.values())[:5]:
            with self.subTest(article=art.id):
                related = cat.related(art.id, limit=5)
                self.assertNotIn(art.id, [r.id for r in related])

    def test_related_for_unknown_id_returns_empty(self):
        cat = loader.load_catalog(force=True)
        self.assertEqual(cat.related("nonexistent/id"), [])


if __name__ == "__main__":
    unittest.main()
