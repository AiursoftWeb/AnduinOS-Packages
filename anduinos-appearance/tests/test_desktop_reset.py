import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


SRC = Path(__file__).resolve().parents[1] / "src"


class DesktopResetTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("dbus-run-session") and shutil.which("dconf"),
        "requires dbus-run-session and dconf",
    )
    def test_reset_removes_selected_overrides_and_preserves_other_settings(self):
        # Use a private D-Bus session and dconf database, never the real desktop.
        with tempfile.TemporaryDirectory(prefix="appearance-reset-test-") as directory:
            profile = Path(directory) / "profile"
            profile.write_text("user-db:user\n", encoding="utf-8")
            environment = dict(
                os.environ,
                DCONF_PROFILE=str(profile),
                XDG_CONFIG_HOME=directory,
                PYTHONPATH=str(SRC),
            )
            result = subprocess.run(
                ["dbus-run-session", "--", sys.executable, "-c", textwrap.dedent("""
                    import subprocess
                    from gi.repository import Gio
                    from anduinos_appearance.settings_transfer import reset_desktop_settings

                    def dconf(*args):
                        return subprocess.run(
                            ['dconf', *args], check=True, capture_output=True, text=True
                        ).stdout.strip()

                    cleared = {
                        '/org/gnome/shell/favorite-apps': "['test.desktop']",
                        '/org/gnome/shell/extensions/test/enabled': 'true',
                        '/org/gnome/desktop/search-providers/sort-order': "['test.desktop']",
                        '/org/gnome/desktop/search-providers/disabled': "['test.desktop']",
                        '/org/gnome/desktop/app-folders/folder-children': "['test']",
                        '/org/gnome/desktop/app-folders/folders/test/name': "'Test folder'",
                        '/org/gnome/desktop/background/picture-uri': "'file:///test.png'",
                        '/org/gnome/desktop/interface/color-scheme': "'prefer-dark'",
                        '/org/gnome/desktop/privacy/remember-recent-files': 'false',
                    }
                    preserved = {
                        '/org/gnome/desktop/input-sources/sources': "[('xkb', 'us')]",
                        '/org/gnome/desktop/peripherals/mouse/left-handed': 'true',
                        '/org/gnome/desktop/a11y/applications/screen-reader-enabled': 'true',
                        '/org/gnome/desktop/remote-desktop/rdp/enable': 'true',
                        '/org/gnome/desktop/notifications/show-banners': 'false',
                        '/org/gnome/desktop/screensaver/lock-delay': 'uint32 99',
                        '/org/gnome/desktop/wm/preferences/num-workspaces': '8',
                        '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/name': "'Test shortcut'",
                    }
                    for key, value in (cleared | preserved).items():
                        dconf('write', key, value)
                    before = {key: dconf('read', key) for key in preserved}

                    reset_desktop_settings()

                    for key in cleared:
                        assert dconf('read', key) == '', key
                    for key, value in before.items():
                        assert dconf('read', key) == value, key
                    search = Gio.Settings.new('org.gnome.desktop.search-providers')
                    assert search.get_value('sort-order') == search.get_default_value('sort-order')
                """)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
