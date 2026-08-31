import ast
import configparser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DconfDefaultsPackageContractTests(unittest.TestCase):
    def test_first_party_proxy_switcher_is_enabled_by_default(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(
            ROOT / "assets/99-anduinos-defaults.gschema.override",
            encoding="utf-8",
        )

        extensions = ast.literal_eval(
            parser["org.gnome.shell"]["enabled-extensions"]
        )
        self.assertIn("proxy-switcher@anduinos.com", extensions)
        self.assertNotIn("ProxySwitcher@flannaghan.com", extensions)

    def test_system_configuration_precedes_file_search_by_default(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(
            ROOT / "assets/99-anduinos-defaults.gschema.override",
            encoding="utf-8",
        )

        providers = ast.literal_eval(
            parser["org.gnome.desktop.search-providers"]["sort-order"]
        )
        self.assertEqual(
            providers[:3],
            [
                "org.gnome.Settings.desktop",
                "com.anduinos.ControlPanel.desktop",
                "org.gnome.Nautilus.desktop",
            ],
        )

    def test_ptyxis_first_window_has_a_valid_terminal_size(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(
            ROOT / "assets/02-ptyxis-terminal.conf",
            encoding="utf-8",
        )

        value = parser["org/gnome/Ptyxis"]["window-size"]
        typed_size = re.fullmatch(
            r"\(uint32 ([1-9][0-9]*), uint32 ([1-9][0-9]*)\)",
            value,
        )
        self.assertIsNotNone(typed_size)
        assert typed_size is not None
        columns, rows = map(int, typed_size.groups())

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
