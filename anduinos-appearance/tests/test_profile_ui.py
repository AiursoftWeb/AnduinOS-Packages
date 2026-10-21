"""Real GTK main-loop checks, without changing the developer's desktop."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProfileUiTests(unittest.TestCase):
    def test_profiles_progress_delay_and_error_recovery(self):
        # CI need not install GNOME Shell itself just to exercise our GTK UI.
        temporary = tempfile.TemporaryDirectory(prefix='appearance-profile-ui-')
        self.addCleanup(temporary.cleanup)
        schema_dir = Path(temporary.name)
        (schema_dir / 'org.gnome.shell.gschema.xml').write_text('''\
            <schemalist>
              <schema id="org.gnome.shell" path="/org/gnome/shell/">
                <key name="enabled-extensions" type="as"><default>[]</default></key>
                <key name="disabled-extensions" type="as"><default>[]</default></key>
              </schema>
            </schemalist>
        ''', encoding='utf-8')
        subprocess.run(['glib-compile-schemas', str(schema_dir)], check=True)
        environment = dict(os.environ, GSETTINGS_BACKEND='memory', LANGUAGE='C',
                           GSETTINGS_SCHEMA_DIR=str(schema_dir),
                           GDK_BACKEND='x11', PYTHONPATH=str(ROOT / 'src'))
        result = subprocess.run(
            ['dbus-run-session', '--', 'xvfb-run', '-a', sys.executable, '-c',
             textwrap.dedent('''\
                import runpy
                import os
                import subprocess
                from pathlib import Path
                import threading
                import time
                from unittest import mock
                import gi
                gi.require_version('Gtk', '4.0')
                gi.require_version('Adw', '1')
                from gi.repository import Gtk, GLib, Gio
                from anduinos_appearance import profiles, layout
                from anduinos_appearance.progress import run_with_progress

                loaded = runpy.run_path('src/anduinos-appearance', run_name='test')
                globals_ = loaded['AppearanceWindow'].__init__.__globals__
                settings = Gio.Settings.new('org.gnome.shell')
                settings.set_strv('enabled-extensions', [*profiles.PANEL_EXTENSIONS, 'ding@rastersoft.com'])
                settings.set_strv('disabled-extensions', [])
                main_thread = threading.get_ident()
                context = GLib.MainContext.default()

                def screenshot(name):
                    directory = os.environ.get('APPEARANCE_TEST_SCREENSHOTS')
                    if not directory:
                        return
                    while context.pending():
                        context.iteration(False)
                    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'x11grab',
                                    '-video_size', '1280x1024', '-i', os.environ['DISPLAY'],
                                    '-frames:v', '1', '-y', str(Path(directory) / name)],
                                   check=True, timeout=10, capture_output=True)

                def drain_until(condition, timeout=3):
                    deadline = time.monotonic() + timeout
                    while not condition():
                        assert time.monotonic() < deadline, 'GTK operation timed out'
                        context.iteration(False)
                        time.sleep(0.002)
                    while context.pending():
                        context.iteration(False)

                def fake_apply(style, position, screen_height):
                    assert threading.get_ident() != main_thread
                    assert screen_height is not None
                    settings.set_strv('disabled-extensions',
                                      list(profiles.PANEL_EXTENSIONS) if style == 'gnome' else [])

                with (
                    mock.patch.object(layout, 'dconf_read', return_value=None),
                    mock.patch.dict(globals_, {'apply_profile': fake_apply,
                                              '_read_group_apps': lambda: True}),
                ):
                    win = loaded['AppearanceWindow'](None)
                    win.present()
                    drain_until(win.get_mapped)
                    page = win.style_page
                    fast = mock.Mock(return_value=True)
                    with mock.patch.dict(globals_, {'apply_style_and_position': fast}):
                        page._on_click(page.btn_sep, 'separated')
                        fast.assert_called_once()
                        assert not win._profile_busy
                    page._on_click(page.btn_gnome, 'gnome')
                    assert win._profile_busy
                    assert win._progress_window.get_mapped()
                    assert win._progress_window.get_modal()
                    assert not win._progress_window.get_deletable()
                    # A second click cannot launch another operation.
                    page._on_click(page.btn10, 'classic')
                    drain_until(lambda: not win._profile_busy)
                    assert win.current_style == 'gnome'
                    assert page.btn_gnome.has_css_class('suggested-action')
                    assert not page.pos_box.get_sensitive()
                    assert not page.behavior_group.get_sensitive()
                    assert not win.widgets_page.activities_row.get_sensitive()
                    win.advanced_page._refresh_ext_rows()
                    assert not win.advanced_page._ext_rows[0][0].get_visible()
                    assert not win.advanced_page._ext_rows[1][0].get_visible()
                    screenshot('gnome.png')
                    assert set(settings.get_strv('disabled-extensions')) == set(profiles.PANEL_EXTENSIONS)
                    assert 'ding@rastersoft.com' in settings.get_strv('enabled-extensions')
                    page._on_pos_toggled(page.pos_btns['left'], 'left')
                    page._on_click(page.btn10, 'classic')
                    drain_until(lambda: not win._profile_busy)
                    assert win.current_style != 'gnome'
                    assert page.pos_box.get_sensitive()
                    assert win.widgets_page.activities_row.get_sensitive()
                    assert win.advanced_page._ext_rows[0][0].get_visible()
                    assert win.advanced_page._ext_rows[1][0].get_visible()
                    assert 'ding@rastersoft.com' in settings.get_strv('enabled-extensions')
                    screenshot('taskbar.png')
                    # External extension changes refresh profile selection.
                    settings.set_strv('enabled-extensions', ['ding@rastersoft.com'])
                    drain_until(lambda: win.current_style == 'gnome')
                    reopened = loaded['AppearanceWindow'](None)
                    assert reopened.current_style == 'gnome'
                    reopened.destroy()
                    def failed(*args):
                        raise profiles.ProfileSwitchError('enable', profiles.PANEL_EXTENSIONS[0])
                    with mock.patch.dict(globals_, {'apply_profile': failed}):
                        page._on_click(page.btn10, 'classic')
                        drain_until(lambda: not win._profile_busy)
                    assert win.current_style == 'gnome'
                    assert win._progress_window is None
                    assert win._profile_failed
                    assert not page.btn_gnome.has_css_class('suggested-action')
                    assert not page.btn10.has_css_class('suggested-action')
                    for item in list(Gtk.Window.get_toplevels()):
                        if item != win:
                            item.destroy()

                    # Independently assert mapped-before-work, >=200 ms delay,
                    # background execution, a live GTK loop, and completion cleanup.
                    for fails in (False, True):
                        events = []
                        started = time.monotonic()
                        def task():
                            events.append(('task', time.monotonic(), threading.get_ident()))
                            time.sleep(0.06)
                            if fails:
                                raise RuntimeError('expected test failure')
                        def finished(error):
                            assert threading.get_ident() == main_thread
                            assert (error is not None) == fails
                            events.append(('done', time.monotonic(), threading.get_ident()))
                        dialog = run_with_progress(win, 'Switching desktop layout…', task, finished)
                        assert dialog.get_mapped()
                        bar = dialog.get_child().get_last_child()
                        assert isinstance(bar, Gtk.ProgressBar)
                        def early():
                            assert not events, 'work began before the initial paint delay'
                            if not fails:
                                screenshot('progress.png')
                            events.append(('early', time.monotonic(), threading.get_ident()))
                            return GLib.SOURCE_REMOVE
                        GLib.timeout_add(100, early)
                        drain_until(lambda: any(e[0] == 'done' for e in events))
                        task_event = next(e for e in events if e[0] == 'task')
                        assert task_event[1] - started >= 0.2
                        assert task_event[2] != main_thread
                        assert events[0][0] == 'early'
                        assert not dialog.get_visible()
                    win.destroy()
             ''')],
            env=environment, cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Traceback (most recent call last)', result.stderr)


if __name__ == '__main__':
    unittest.main()
