"""The extension switches must include system defaults, not just dconf overrides."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


class ExtensionSwitchTests(unittest.TestCase):
    def test_live_default_and_external_changes_are_reflected(self):
        with tempfile.TemporaryDirectory(prefix="appearance-extensions-test-") as directory:
            schema_dir = Path(directory) / "schemas"
            schema_dir.mkdir()
            (schema_dir / "org.gnome.shell.gschema.xml").write_text(
                textwrap.dedent("""\
                    <schemalist>
                      <schema id="org.gnome.shell" path="/org/gnome/shell/">
                        <key name="enabled-extensions" type="as">
                          <default>['ding@rastersoft.com']</default>
                        </key>
                      </schema>
                    </schemalist>
                """), encoding="utf-8")
            subprocess.run(["glib-compile-schemas", str(schema_dir)], check=True)
            profile = Path(directory) / "profile"
            profile.write_text("user-db:user\n", encoding="utf-8")
            environment = dict(
                os.environ,
                DCONF_PROFILE=str(profile),
                GSETTINGS_SCHEMA_DIR=str(schema_dir),
                XDG_CONFIG_HOME=directory,
                PYTHONPATH=str(SRC),
            )
            result = subprocess.run(
                ["dbus-run-session", "--", "xvfb-run", "-a", sys.executable,
                 "-c", textwrap.dedent("""\
                    import runpy
                    import subprocess
                    from gi.repository import Gio, GLib
                    from pathlib import Path

                    app = runpy.run_path(
                        str(Path('src/anduinos-appearance').resolve()),
                        run_name='AnduinOSAppearanceTest'
                    )
                    page = app['PanelWidgetsPage'](object())
                    ding = page._widget_rows['ding@rastersoft.com']
                    assert subprocess.run(
                        ['dconf', 'read', '/org/gnome/shell/enabled-extensions'],
                        capture_output=True, text=True, check=True
                    ).stdout.strip() == ''
                    assert ding.get_active(), 'system default must show DING as enabled'

                    settings = Gio.Settings.new('org.gnome.shell')
                    settings.set_strv('enabled-extensions', [])
                    while GLib.MainContext.default().pending():
                        GLib.MainContext.default().iteration(False)
                    assert not ding.get_active(), 'external disable must refresh the switch'

                    settings.reset('enabled-extensions')
                    while GLib.MainContext.default().pending():
                        GLib.MainContext.default().iteration(False)
                    assert ding.get_active(), 'reset must restore the system default'
                """)],
                env=environment,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
