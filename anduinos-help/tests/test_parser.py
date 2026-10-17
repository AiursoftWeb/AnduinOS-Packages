"""Tests for the markdown parser — verifies the IR contract.

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

from anduinos_help.docs import parser  # noqa: E402


class ParserContractTests(unittest.TestCase):
    """Verify what the parser produces for given markdown inputs."""

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(parser.parse(""), [])

    def test_heading_block_has_level_text_and_id(self):
        blocks = parser.parse("# Hello World")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "heading")
        self.assertEqual(blocks[0]["level"], 1)
        self.assertEqual(blocks[0]["text"], "Hello World")
        self.assertEqual(blocks[0]["id"], "hello-world")

    def test_code_block_includes_language_and_text(self):
        md = "```bash\nsudo apt update\n```"
        blocks = parser.parse(md)
        self.assertEqual(blocks[0]["type"], "code")
        self.assertEqual(blocks[0]["language"], "bash")
        self.assertEqual(blocks[0]["text"], "sudo apt update")

    def test_root_prompt_detection(self):
        """Code blocks whose first non-empty line starts with '#' (and is
        not a shebang) must be flagged as root-prompt — they describe
        commands that should NOT be typed verbatim."""
        md = "```bash\n# apt install foo\n```"
        blocks = parser.parse(md)
        self.assertTrue(blocks[0]["is_root"])

    def test_warning_callout_is_classified_as_warning(self):
        """``> [!WARNING]`` blockquotes must become callout blocks of
        kind 'warning', not plain blockquotes."""
        md = "> [!WARNING]\n> This is dangerous."
        blocks = parser.parse(md)
        self.assertEqual(blocks[0]["type"], "callout")
        self.assertEqual(blocks[0]["kind"], "warning")
        # The text after the marker must be preserved
        body_text = blocks[0]["blocks"][0]["text"] if blocks[0]["blocks"] else ""
        self.assertIn("dangerous", body_text)

    def test_caution_is_normalised_to_warning(self):
        """``[!CAUTION]`` is a MkDocs alias for ``[!WARNING]``."""
        md = "> [!CAUTION]\n> Be careful."
        blocks = parser.parse(md)
        self.assertEqual(blocks[0]["kind"], "warning")

    def test_table_block_has_headers_and_rows(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        blocks = parser.parse(md)
        self.assertEqual(blocks[0]["type"], "table")
        self.assertEqual(blocks[0]["headers"], ["A", "B"])
        self.assertEqual(blocks[0]["rows"], [["1", "2"], ["3", "4"]])


if __name__ == "__main__":
    unittest.main()
