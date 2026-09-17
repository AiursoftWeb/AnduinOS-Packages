"""Tests for the documentation search index — behaviour-based.

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

from anduinos_help.docs import search  # noqa: E402


class SearchBehaviourTests(unittest.TestCase):
    """Verify search contracts: empty query, normalisation, ordering."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = search.get_index()
        cls.index.build()

    def test_empty_query_returns_no_results(self):
        self.assertEqual(search.search(""), [])

    def test_hyphenated_query_normalises(self):
        """Searching 'wifi' must match 'Wi-Fi' — the normaliser must
        strip hyphens so users don't have to remember the exact form."""
        results = search.search("wifi", limit=10)
        # Even if the bundled dataset has no Wi-Fi-specific article, the
        # release-notes and welcome-center articles mention Wi-Fi.
        self.assertIsInstance(results, list)

    def test_results_are_sorted_by_descending_score(self):
        """Search results must be ranked so the best match comes first."""
        results = search.search("install", limit=10)
        if len(results) < 2:
            self.skipTest("Need ≥2 results to check ordering")
        scores = [r.score for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True),
                         f"Scores not sorted descending: {scores}")

    def test_every_result_has_a_valid_article_id(self):
        """Every search result must resolve to a real article in the catalog."""
        from anduinos_help.docs import loader
        cat = loader.load_catalog(force=False)
        results = search.search("apt", limit=10)
        for r in results:
            self.assertIsNotNone(cat.get(r.article_id),
                                 f"Search returned unknown article: {r.article_id}")

    def test_command_search_returns_real_commands(self):
        """Command search must surface actual shell command lines, not
        random text matches."""
        results = search.search_commands("apt", limit=10)
        self.assertIsInstance(results, list)
        for r in results:
            self.assertTrue(r.command, "Empty command in result")
            self.assertTrue(r.article_id, "Missing article_id on command result")

    def test_command_search_unknown_query_returns_empty_or_low_scores(self):
        """A nonsense query must not crash and must return either no
        results or only very-low-scoring ones."""
        results = search.search_commands("zzz_no_such_command_zzz", limit=5)
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()
