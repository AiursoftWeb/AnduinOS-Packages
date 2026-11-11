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
    def test_icon_choices_preserve_other_settings_and_follow_external_changes(self):
        with tempfile.TemporaryDirectory(prefix="appearance-icons-test-") as directory:
            schema_dir = Path(directory) / "schemas"
            schema_dir.mkdir()
            (schema_dir / "test.gschema.xml").write_text('''
                <schemalist>
                  <schema id="org.gnome.shell" path="/org/gnome/shell/">
                    <key name="enabled-extensions" type="as"><default>[]</default></key>
                  </schema>
                  <schema id="org.gnome.desktop.interface" path="/org/gnome/desktop/interface/">
                    <key name="icon-theme" type="s"><default>'Fluent-dark'</default></key>
                    <key name="gtk-theme" type="s"><default>'Fluent-round-Dark'</default></key>
                    <key name="cursor-theme" type="s"><default>'Fluent-dark-cursors'</default></key>
                  </schema>
                </schemalist>
            ''', encoding="utf-8")
            subprocess.run(["glib-compile-schemas", str(schema_dir)], check=True)
            environment = dict(os.environ, GSETTINGS_BACKEND="memory", LANGUAGE="C",
                               GSETTINGS_SCHEMA_DIR=str(schema_dir), PYTHONPATH=str(SRC),
                               GDK_BACKEND="x11")
            result = subprocess.run(
                ["dbus-run-session", "--", "xvfb-run", "-a", sys.executable,
                 "-c", textwrap.dedent('''
                    import runpy
                    from unittest import mock
                    from gi.repository import Gio, GLib

                    loaded = runpy.run_path('src/anduinos-appearance', run_name='test')
                    class Window:
                        messages = []
                        def _toast(self, message):
                            self.messages.append(message)
                    window = Window()
                    page = loaded['PanelWidgetsPage'](window)
                    settings = Gio.Settings.new('org.gnome.desktop.interface')
                    buttons = page.icon_buttons
                    def drain():
                        while GLib.MainContext.default().pending():
                            GLib.MainContext.default().iteration(False)

                    assert settings.get_user_value('icon-theme') is None
                    assert buttons['Fluent-dark'].get_active()
                    buttons['Adwaita'].emit('clicked')
                    drain()
                    assert settings.get_string('icon-theme') == 'Adwaita'
                    assert buttons['Adwaita'].get_active()
                    assert not buttons['Fluent-dark'].get_active()
                    assert settings.get_user_value('gtk-theme') is None
                    assert settings.get_user_value('cursor-theme') is None
                    assert page._shell_settings.get_user_value('enabled-extensions') is None

                    reopened = loaded['PanelWidgetsPage'](object())
                    assert reopened.icon_buttons['Adwaita'].get_active()
                    buttons['Fluent-dark'].emit('clicked')
                    drain()
                    assert settings.get_string('icon-theme') == 'Fluent-dark'
                    assert reopened.icon_buttons['Fluent-dark'].get_active()

                    settings.set_string('icon-theme', 'Fluent-blue')
                    drain()
                    assert buttons['Fluent-dark'].get_active()
                    assert settings.get_string('icon-theme') == 'Fluent-blue'
                    settings.set_string('icon-theme', 'Papirus')
                    drain()
                    assert not any(button.get_active() for button in buttons.values())
                    assert settings.get_string('icon-theme') == 'Papirus'
                    settings.reset('icon-theme')
                    drain()
                    assert buttons['Fluent-dark'].get_active()
                    assert settings.get_user_value('icon-theme') is None
                    with mock.patch.object(page._interface_settings, 'set_string', return_value=False):
                        buttons['Adwaita'].emit('clicked')
                    assert window.messages == ['Failed to change icon theme.']
                    assert buttons['Fluent-dark'].get_active()
                    assert not buttons['Adwaita'].get_active()
                    assert settings.get_user_value('icon-theme') is None
                    with mock.patch.object(page._interface_settings, 'is_writable', return_value=False):
                        page._sync_icon_theme()
                        assert not any(button.get_sensitive() for button in buttons.values())
                ''')], env=environment, cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
                    from unittest import mock
                    from gi.repository import Gio, GLib
                    from pathlib import Path

                    app = runpy.run_path(
                        str(Path('src/anduinos-appearance').resolve()),
                        run_name='AnduinOSAppearanceTest'
                    )
                    globals_ = app['PanelWidgetsPage'].__init__.__globals__
                    with mock.patch.dict(globals_, {'extension_settings': lambda *_: None}):
                        page = app['PanelWidgetsPage'](object())
                    assert not page.layout_picker_row.get_sensitive(), \
                        'missing optional tiling settings must not crash Appearance'
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

    def test_layout_picker_default_user_choice_and_external_changes(self):
        with tempfile.TemporaryDirectory(prefix="appearance-tiling-test-") as directory:
            root = Path(directory)
            schemas = root / 'schemas'
            schemas.mkdir()
            (schemas / 'shell.gschema.xml').write_text('''\
                <schemalist>
                  <schema id="org.gnome.shell" path="/org/gnome/shell/">
                    <key name="enabled-extensions" type="as"><default>[]</default></key>
                  </schema>
                  <schema id="org.gnome.desktop.interface" path="/org/gnome/desktop/interface/">
                    <key name="icon-theme" type="s"><default>'Fluent-dark'</default></key>
                  </schema>
                </schemalist>
            ''')
            # Extension schemas are normally local to their install directory,
            # rather than registered in the global GSettings schema source.
            extension_schemas = root / 'share/gnome-shell/extensions/tiling-assistant@leleat-on-github/schemas'
            extension_schemas.mkdir(parents=True)
            (extension_schemas / 'tiling.gschema.xml').write_text('''\
                <schemalist>
                  <schema id="org.gnome.shell.extensions.tiling-assistant"
                          path="/org/gnome/shell/extensions/tiling-assistant/">
                    <key name="enable-layout-picker" type="b"><default>false</default></key>
                  </schema>
                </schemalist>
            ''')
            for path in (schemas, extension_schemas):
                subprocess.run(['glib-compile-schemas', str(path)], check=True)
            environment = dict(os.environ, GSETTINGS_BACKEND='memory',
                               GSETTINGS_SCHEMA_DIR=str(schemas),
                               XDG_DATA_HOME=str(root / 'share'),
                               XDG_DATA_DIRS=str(root / 'system-share'),
                               GDK_BACKEND='x11', LANGUAGE='C', PYTHONPATH=str(SRC))
            result = subprocess.run(
                ['dbus-run-session', '--', 'xvfb-run', '-a', sys.executable, '-c',
                 textwrap.dedent('''\
                    import runpy
                    from gi.repository import Gio, GLib
                    app = runpy.run_path('src/anduinos-appearance', run_name='test')
                    page = app['PanelWidgetsPage'](object())
                    row = page.layout_picker_row
                    settings = page._tiling_settings
                    key = 'enable-layout-picker'

                    def drain():
                        while GLib.MainContext.default().pending():
                            GLib.MainContext.default().iteration(False)

                    assert row.get_sensitive()
                    assert not row.get_active(), 'show the effective system default'
                    assert settings.get_user_value(key) is None, \
                        'opening Appearance must not save a user override'
                    row.set_active(True)
                    drain()
                    assert settings.get_boolean(key), 'the switch must enable the real feature'
                    reopened = app['PanelWidgetsPage'](object())
                    assert reopened.layout_picker_row.get_active(), \
                        'an explicit user choice must survive reopening'
                    settings.set_boolean(key, False)
                    drain()
                    assert not row.get_active() and not reopened.layout_picker_row.get_active(), \
                        'changes from extension preferences must refresh both windows'
                    settings.reset(key)
                    drain()
                    assert not row.get_active()
                    assert settings.get_user_value(key) is None
                ''')], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
