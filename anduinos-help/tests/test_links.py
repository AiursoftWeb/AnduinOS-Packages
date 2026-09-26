"""Tests for the URL allowlist — security boundary.

The link allowlist is a safety boundary: clicking an in-app link must
never spawn an arbitrary command or open an attacker-controlled page.
The allowlist is therefore part of the security contract, not just a UX
convenience.

Run from the package directory:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src LANGUAGE=C \\
        python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import pathlib
import sys
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from anduinos_help.utils import links  # noqa: E402


class AllowlistSecurityTests(unittest.TestCase):
    """Verify that the URL allowlist blocks dangerous schemes and
    unknown domains, and accepts the official AnduinOS domains."""

    def test_javascript_scheme_is_rejected(self):
        """A javascript: URL must NEVER be classified as safe — it
        would execute arbitrary code if passed to a browser."""
        self.assertFalse(links.is_safe_external("javascript:alert(1)"))
        self.assertFalse(links.is_safe_external("javascript:void(0)"))

    def test_data_uri_is_rejected(self):
        """data: URIs can carry payloads — block by default."""
        self.assertFalse(links.is_safe_external("data:text/html,<script>"))

    def test_official_docs_domain_is_allowed(self):
        self.assertTrue(links.is_safe_external("https://docs.anduinos.com/Install/Foo.html"))

    def test_github_domain_is_allowed(self):
        """Official issue tracker lives on github.com — must be allowed."""
        self.assertTrue(links.is_safe_external("https://github.com/AiursoftWeb/AnduinOS-2/issues"))

    def test_unknown_domain_is_rejected(self):
        """Arbitrary third-party domains must NOT be opened by the app
        without an explicit user action (and we don't add that action
        in markup)."""
        self.assertFalse(links.is_safe_external("https://evil.example.com/"))

    def test_internal_link_scheme_is_recognised(self):
        """In-app navigation URIs must be parsed correctly — a click
        on a sibling-article link must dispatch in-app, not to a browser."""
        self.assertTrue(links.is_internal_link("anduinos-help://article/install/download"))
        self.assertFalse(links.is_internal_link("https://example.com/"))

    def test_parse_internal_article_extracts_id(self):
        self.assertEqual(
            links.parse_internal_article("anduinos-help://article/install/download"),
            "install/download",
        )
        self.assertIsNone(links.parse_internal_article("https://example.com/"))


if __name__ == "__main__":
    unittest.main()
