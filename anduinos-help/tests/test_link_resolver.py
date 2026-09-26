"""Tests for the internal-link resolver — sibling .md links must resolve
to article IDs so in-article navigation works.

Run from the package directory:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src LANGUAGE=C \\
        python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ASSETS = pathlib.Path(__file__).resolve().parents[1] / "assets"
if ASSETS.is_dir():
    os.environ.setdefault("ANDUINOS_HELP_DATA_DIR", str(ASSETS))

from anduinos_help.docs import loader  # noqa: E402


class LinkResolverBehaviourTests(unittest.TestCase):
    """Verify the contract: clicking a sibling link must navigate
    to a real bundled article, not silently fail."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cat = loader.load_catalog(force=True)

    def test_resolver_rejects_external_urls(self):
        """External URLs must never be rewritten as internal links."""
        art = next(iter(self.cat.articles.values()))
        resolver = loader.ArticleLinkResolver(art, self.cat)
        self.assertIsNone(resolver.resolve("https://example.com/foo"))
        self.assertIsNone(resolver.resolve("mailto:foo@example.com"))

    def test_resolver_rejects_non_md_html_links(self):
        """Image links or bare slugs must not be rewritten."""
        art = next(iter(self.cat.articles.values()))
        resolver = loader.ArticleLinkResolver(art, self.cat)
        self.assertIsNone(resolver.resolve("image.png"))
        self.assertIsNone(resolver.resolve("plain-text"))

    def test_resolver_rejects_empty_input(self):
        art = next(iter(self.cat.articles.values()))
        resolver = loader.ArticleLinkResolver(art, self.cat)
        self.assertIsNone(resolver.resolve(""))
        self.assertIsNone(resolver.resolve(None))

    def test_at_least_one_bundled_article_has_a_resolvable_internal_link(self):
        """If no article has internal links, the resolver is broken or the
        dataset is unusable for in-app navigation. Either is a regression."""
        found_internal = False
        for art in self.cat.articles.values():
            blocks = loader.load_article_blocks(art)
            for block in blocks:
                if block.get("type") == "paragraph":
                    for span in block.get("spans", []):
                        url = span.get("url")
                        if url and url.startswith("anduinos-help://article/"):
                            aid = url[len("anduinos-help://article/"):]
                            # The target must exist in the catalog
                            self.assertIsNotNone(self.cat.get(aid),
                                                 f"Resolved to unknown article: {aid}")
                            found_internal = True
                            break
                if found_internal:
                    break
            if found_internal:
                break
        self.assertTrue(found_internal,
                        "No article has a resolvable internal link — resolver broken")


if __name__ == "__main__":
    unittest.main()
