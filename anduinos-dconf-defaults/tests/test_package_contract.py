import ast
import configparser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DconfDefaultsPackageContractTests(unittest.TestCase):
    def test_ptyxis_first_window_has_a_valid_terminal_size(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(
            ROOT / "assets/02-ptyxis-terminal.conf",
            encoding="utf-8",
        )

        columns, rows = ast.literal_eval(
            parser["org/gnome/Ptyxis"]["window-size"]
        )

        self.assertGreaterEqual(columns, 1)
        self.assertGreaterEqual(rows, 1)

    def test_project_installs_the_ptyxis_dconf_fragment(self):
        project = (
            ROOT / "anduinos-dconf-defaults.aosproj"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'IncludeFile Include="assets/02-ptyxis-terminal.conf"',
            project,
        )
        self.assertIn(
            'Target="/etc/dconf/db/anduinos.d/02-ptyxis-terminal.conf"',
            project,
        )


if __name__ == "__main__":
    unittest.main()
