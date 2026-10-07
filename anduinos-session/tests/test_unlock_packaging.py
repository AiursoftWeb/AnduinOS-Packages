"""Keep unlock theme ownership, defaults and accessibility fallback in sync."""
import configparser
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class UnlockPackagingTests(unittest.TestCase):
    def test_runs_only_in_unlock_mode(self):
        metadata = json.loads((ROOT / 'assets/unlock-theme@anduinos.com/metadata.json').read_text())
        self.assertEqual(metadata['session-modes'], ['unlock-dialog'])
        self.assertEqual(metadata['shell-version'], ['46', '50'])
        defaults = configparser.ConfigParser(interpolation=None)
        defaults.read(ROOT.parent / 'anduinos-dconf-defaults/assets/99-anduinos-defaults.gschema.override')
        self.assertIn("'" + metadata['uuid'] + "'", defaults['org.gnome.shell']['enabled-extensions'])

    def test_session_package_owns_extension_and_reuses_readability(self):
        project = ET.parse(ROOT / 'anduinos-session.aosproj')
        folder = project.find(".//IncludeFolder[@Include='assets/unlock-theme@anduinos.com']")
        self.assertEqual(folder.get('Target'), '/usr/share/gnome-shell/extensions/unlock-theme@anduinos.com')
        overlays = project.findall(".//IncludeFile[@Include='assets/anduinos-lock-screen.css']")
        self.assertEqual({item.get('Target') for item in overlays}, {
            '/usr/share/gnome-shell/theme/anduinos-lock-screen.css',
            '/usr/share/gnome-shell/extensions/unlock-theme@anduinos.com/readability.css',
        })
        self.assertIsNotNone(project.find(".//TestCommand[@Profile='anduinos-package-release-test']"))

    def test_high_contrast_has_no_readability_overrides(self):
        css = (ROOT / 'assets/anduinos-shell-high-contrast.css').read_text()
        self.assertIn('gnome-shell-high-contrast.css', css)
        self.assertNotIn('anduinos-lock-screen.css', css)


if __name__ == '__main__':
    unittest.main()
