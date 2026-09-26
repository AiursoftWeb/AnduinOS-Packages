"""Tests for the AnduinOS version detection module."""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from anduinos_help.system import version  # noqa: E402


class VersionDetectionTests(unittest.TestCase):
    """Verify that we correctly detect AnduinOS and fall back gracefully
    on other systems (for development/testing)."""

    def test_detect_returns_a_system_version_object(self):
        sv = version.detect()
        self.assertIsNotNone(sv)
        self.assertIsInstance(sv.is_anduinos, bool)

    def test_applicable_doc_version_returns_a_known_label(self):
        """The version label determines which articles are shown — it
        must be either '1.x' or '2.x' for the current release line."""
        v = version.applicable_doc_version()
        self.assertIn(v, {"1.x", "2.x"})

    def test_major_version_extracts_x_label(self):
        sv = version.SystemVersion(
            is_anduinos=True,
            anduinos_version="2.0.3",
            anduinos_codename=None, base_distro=None, base_version=None,
            kernel=None, desktop=None, session_type=None,
        )
        self.assertEqual(sv.major_version(), "2.x")

    def test_major_version_returns_none_when_unknown(self):
        sv = version.SystemVersion(
            is_anduinos=False,
            anduinos_version=None,
            anduinos_codename=None, base_distro=None, base_version=None,
            kernel=None, desktop=None, session_type=None,
        )
        self.assertIsNone(sv.major_version())

    def test_parse_os_release_handles_quoted_values(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix="-os-release", delete=False) as f:
            f.write('NAME="AnduinOS"\nVERSION_ID="2.0.3"\nID=anduin\n')
            path = f.name
        try:
            parsed = version._parse_os_release(path)
            self.assertEqual(parsed["NAME"], "AnduinOS")
            self.assertEqual(parsed["VERSION_ID"], "2.0.3")
            self.assertEqual(parsed["ID"], "anduin")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
