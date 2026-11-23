"""Tests for the SQLite-backed document store.

These verify the store/search/nav contract without requiring a live
git clone — they insert fixture documents directly.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class StoreTests(unittest.TestCase):
    """Verify the SQLite store contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_DATA_HOME"] = cls._tmp.name + "/data"
        os.environ["XDG_STATE_HOME"] = cls._tmp.name + "/state"
        os.environ["XDG_CACHE_HOME"] = cls._tmp.name + "/cache"
        # Force re-import of paths so it picks up the new XDG dirs
        from anduinos_help.docs import store
        store.init_db()
        # Insert 3 fixture documents
        store.upsert_document(
            file_path="Install/Download-AnduinOS.md",
            title="Download AnduinOS",
            category="Install",
            content="# Download AnduinOS\n\nBefore installing, download the ISO file.",
            file_last_modified="2026-09-15T10:00:00",
            source_url="https://docs.anduinos.com/Install/Download-AnduinOS.html",
            source_repo_url="https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/Docs/Install/Download-AnduinOS.md",
        )
        store.upsert_document(
            file_path="Install/Burn-A-USB-Stick.md",
            title="Burn a USB Stick",
            category="Install",
            content="# Burn a USB Stick\n\nUse dd or Ventoy to write the ISO to a USB stick.",
            file_last_modified="2026-09-15T10:00:00",
            source_url="https://docs.anduinos.com/Install/Burn-A-USB-Stick.html",
            source_repo_url="https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/Docs/Install/Burn-A-USB-Stick.md",
        )
        store.upsert_document(
            file_path="Skills/Introduction.md",
            title="Skills Introduction",
            category="Skills",
            content="# Skills Introduction\n\nAdvanced AnduinOS administration skills.",
            file_last_modified="2026-09-15T10:00:00",
            source_url="https://docs.anduinos.com/Skills/Introduction.html",
            source_repo_url="https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/Docs/Skills/Introduction.md",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_document_count(self):
        from anduinos_help.docs import store
        self.assertEqual(store.document_count(), 3)

    def test_list_documents_unfiltered(self):
        from anduinos_help.docs import store
        docs = store.list_documents()
        self.assertEqual(len(docs), 3)

    def test_list_documents_by_category(self):
        from anduinos_help.docs import store
        install_docs = store.list_documents(category="Install")
        self.assertEqual(len(install_docs), 2)
        for d in install_docs:
            self.assertEqual(d.category, "Install")

    def test_get_document_by_path(self):
        from anduinos_help.docs import store
        d = store.get_document_by_path("Install/Download-AnduinOS.md")
        self.assertIsNotNone(d)
        self.assertEqual(d.title, "Download AnduinOS")
        self.assertEqual(d.category, "Install")
        self.assertIn("ISO", d.content)

    def test_get_document_by_id(self):
        from anduinos_help.docs import store
        # Article id is lowercased path without .md
        d = store.get_document_by_id("install/download-anduinos")
        self.assertIsNotNone(d)
        self.assertEqual(d.title, "Download AnduinOS")

    def test_search_returns_results(self):
        from anduinos_help.docs import store
        results = store.search("download")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].title, "Download AnduinOS")

    def test_search_multi_term(self):
        from anduinos_help.docs import store
        results = store.search("usb stick")
        self.assertGreater(len(results), 0)
        titles = [r.title for r in results]
        self.assertIn("Burn a USB Stick", titles)

    def test_search_empty_query_returns_empty(self):
        from anduinos_help.docs import store
        self.assertEqual(store.search(""), [])

    def test_list_categories(self):
        from anduinos_help.docs import store
        cats = dict(store.list_categories())
        self.assertIn("Install", cats)
        self.assertIn("Skills", cats)
        self.assertEqual(cats["Install"], 2)
        self.assertEqual(cats["Skills"], 1)

    def test_mark_missing_deleted(self):
        from anduinos_help.docs import store
        # Mark only the Skills doc as found — Install docs should be soft-deleted
        deleted = store.mark_missing_deleted({"Skills/Introduction.md"})
        self.assertEqual(deleted, 2)
        # Restore for other tests
        store.upsert_document(
            file_path="Install/Download-AnduinOS.md",
            title="Download AnduinOS",
            category="Install",
            content="# Download AnduinOS\n\nBefore installing, download the ISO file.",
            file_last_modified="2026-09-15T10:00:00",
        )
        store.upsert_document(
            file_path="Install/Burn-A-USB-Stick.md",
            title="Burn a USB Stick",
            category="Install",
            content="# Burn a USB Stick\n\nUse dd or Ventoy to write the ISO to a USB stick.",
            file_last_modified="2026-09-15T10:00:00",
        )


if __name__ == "__main__":
    unittest.main()
