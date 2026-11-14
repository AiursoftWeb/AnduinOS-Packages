"""Tests for the diagnostics module — security boundary.

The diagnostics module collects system information for the "Report a
Problem" workflow. It must NEVER include passwords, tokens, API keys,
private files, or personal documents — this is a security boundary.

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

from anduinos_help.system import diagnostics  # noqa: E402


class DiagnosticsSecurityTests(unittest.TestCase):
    """Verify that the diagnostic report NEVER leaks sensitive data."""

    def setUp(self) -> None:
        self.report = diagnostics.collect()
        self.text = self.report.to_text()
        self.markdown = self.report.to_markdown()

    def test_report_does_not_contain_sensitive_file_paths(self):
        """Sensitive system files must never appear in the report."""
        for path in ("/etc/shadow", "/etc/sudoers", "/etc/gshadow"):
            self.assertNotIn(path, self.text, f"Report leaked path: {path}")
            self.assertNotIn(path, self.markdown, f"Report leaked path: {path}")

    def test_report_does_not_contain_secret_keywords(self):
        """Common secret-bearing field names must not appear in output."""
        for keyword in ("password", "passwd", "api_key", "apikey", "token", "secret"):
            self.assertNotIn(keyword, self.text.lower(),
                             f"Report contains secret keyword: {keyword}")

    def test_report_contains_required_system_info(self):
        """Without OS and kernel info, a bug report is useless."""
        self.assertIn("OS", self.report.system)
        self.assertIn("Kernel", self.report.system)
        self.assertIn("Architecture", self.report.system)

    def test_report_contains_package_manager_status(self):
        """Knowing which package managers are available helps triage."""
        for name in ("apt", "dpkg", "flatpak", "snap"):
            self.assertIn(name, self.report.package_managers,
                          f"Missing package manager status: {name}")

    def test_safe_read_returns_empty_for_sensitive_files(self):
        """The safe_read helper must explicitly skip sensitive paths."""
        self.assertEqual(diagnostics._safe_read("/etc/shadow"), "")
        self.assertEqual(diagnostics._safe_read("/etc/sudoers"), "")

    def test_safe_read_returns_empty_for_nonexistent_files(self):
        self.assertEqual(diagnostics._safe_read("/nonexistent/file.txt"), "")


if __name__ == "__main__":
    unittest.main()
