import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]


class LiveShortcutAssetTests(unittest.TestCase):
    def test_shortcut_is_guarded_by_casper_runtime_state(self):
        script = (
            PACKAGE / "assets/anduinos-installer-beta-live-shortcut"
        ).read_text()
        self.assertIn("[ -d /cdrom ] || exit 0", script)
        self.assertIn("grep -qw 'boot=casper' /proc/cmdline || exit 0", script)
        self.assertIn('"$HOME"/*', script)
        self.assertNotIn("/etc/skel", script)

    def test_shortcut_uses_its_packaged_box_icon_and_is_marked_trusted(self):
        launcher = (
            PACKAGE / "assets/anduinos-installer-beta.desktop"
        ).read_text()
        icon = PACKAGE / "assets/anduinos-installer-beta.svg"
        script = (
            PACKAGE / "assets/anduinos-installer-beta-live-shortcut"
        ).read_text()
        self.assertIn("Icon=anduinos-installer-beta", launcher)
        self.assertIn("StartupWMClass=com.anduinos.InstallerBeta", launcher)
        self.assertTrue(icon.is_file())
        self.assertIn("<svg", icon.read_text())
        self.assertIn(
            "/usr/share/applications/com.anduinos.InstallerBeta.desktop",
            script,
        )
        self.assertIn("metadata::trusted true", script)

    def test_live_login_starts_the_installer_after_creating_the_shortcut(self):
        script = (
            PACKAGE / "assets/anduinos-installer-beta-live-shortcut"
        ).read_text()
        shortcut = script.index('install -m 0755 "$source" "$destination"')
        launch = script.index("exec /usr/bin/anduinos-installer-beta")
        self.assertLess(shortcut, launch)
        self.assertIn(
            "[ -x /usr/bin/anduinos-installer-beta ] || exit 0",
            script,
        )

    def test_desktop_file_target_matches_gtk_application_id(self):
        project = (
            PACKAGE / "anduinos-installer-beta.aosproj"
        ).read_text()
        main = (PACKAGE / "src/main.py").read_text()
        self.assertIn('APP_ID = "com.anduinos.InstallerBeta"', main)
        self.assertIn(
            'Target="/usr/share/applications/com.anduinos.InstallerBeta.desktop"',
            project,
        )
        self.assertIn(
            'Gtk.Window.set_default_icon_name(ICON_NAME)',
            main,
        )

    def test_desktop_launcher_has_all_28_official_localizations(self):
        launcher = (
            PACKAGE / "assets/anduinos-installer-beta.desktop"
        ).read_text()
        expected = {
            "ar",
            "da",
            "de",
            "el",
            "en_GB",
            "en_US",
            "es",
            "fi",
            "fr",
            "hi",
            "id",
            "it",
            "ja",
            "ko",
            "nl",
            "pl",
            "pt",
            "pt_BR",
            "ro",
            "ru",
            "sv",
            "th",
            "tr",
            "uk",
            "vi",
            "zh_CN",
            "zh_HK",
            "zh_TW",
        }
        for key in ("Name", "Comment"):
            actual = {
                line.removeprefix(f"{key}[").split("]", 1)[0]
                for line in launcher.splitlines()
                if line.startswith(f"{key}[")
            }
            self.assertEqual(actual, expected)

    def test_welcome_page_uses_the_packaged_box_icon(self):
        pages = (PACKAGE / "src/pages.py").read_text()
        self.assertIn(
            'Gtk.Image.new_from_icon_name("anduinos-installer-beta")',
            pages,
        )
        self.assertNotIn(
            'Gtk.Image.new_from_icon_name("computer-symbolic")',
            pages,
        )

    def test_autostart_is_hidden_and_gnome_only(self):
        autostart = (
            PACKAGE
            / "assets/anduinos-installer-beta-live-shortcut.desktop"
        ).read_text()
        self.assertIn("OnlyShowIn=GNOME;", autostart)
        self.assertIn("NoDisplay=true", autostart)
        self.assertIn("X-GNOME-Autostart-enabled=true", autostart)
        self.assertIn(
            "Exec=/usr/lib/anduinos-installer-beta/create-live-shortcut",
            autostart,
        )
