import tempfile
import unittest
from pathlib import Path

from anduinos_rescue_center.os_release import read_os_release


class OsReleaseTests(unittest.TestCase):
    def test_parses_data_without_executing_shell_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "os-release"
            path.write_text(
                'ID=anduinos\nPRETTY_NAME="AnduinOS 2.0.3"\nBAD=$(touch /tmp/no)\n',
                encoding="utf-8",
            )
            values = read_os_release(path)
            self.assertEqual(values["ID"], "anduinos")
            self.assertEqual(values["PRETTY_NAME"], "AnduinOS 2.0.3")
            self.assertNotIn("BAD", values)

    def test_missing_file_is_not_an_operating_system(self):
        self.assertEqual(read_os_release(Path("/definitely/missing/os-release")), {})
