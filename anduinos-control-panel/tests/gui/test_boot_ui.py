"""Run with xvfb-run and CONTROL_PANEL_UI_TESTS=1; subprocesses are mocked."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))


class BootUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('CONTROL_PANEL_UI_TESTS') != '1':
            raise RuntimeError('Run through the gui test profile with an isolated display.')
        from anduinos_control_panel import app
        cls.module = app
        app.Adw.init()
        cls.application = app.Adw.Application(application_id='com.anduinos.ControlPanel.BootTest')
        cls.application.register(None)

    def setUp(self):
        app = self.module
        self.patches = [
            patch.object(app.ControlPanelWindow, '_rebuild_categories'),
            patch.object(app, '_', side_effect=lambda value: value),
            patch.object(app, 'read_grub_timeouts', return_value=type('Timeout', (), {'normal': 10, 'after_interrupted_boot': 10})()),
            patch.object(app, 'read_grub_display_mode', return_value='automatic'),
        ]
        for item in self.patches:
            item.start()
        self.window = app.ControlPanelWindow(self.application)

    @staticmethod
    def walk(widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from BootUiTests.walk(child)
            child = child.get_next_sibling()

    def tearDown(self):
        if self.window._boot_settings_window is not None:
            self.window._boot_settings_window.destroy()
        self.window.destroy()
        for item in reversed(self.patches):
            item.stop()

    def settle(self):
        deadline = time.monotonic() + 3
        while not self.buttons['Close'].get_sensitive():
            while self.module.GLib.MainContext.default().iteration(False):
                pass
            if time.monotonic() > deadline:
                self.fail('UI worker did not finish')
            time.sleep(.005)

    def load(self, current='gnulinux-simple-abc', return_code=0):
        data = {'entries': [{'id': 'gnulinux-simple-abc', 'title': 'AnduinOS'}, {'id': 'osprober-efi-1234', 'title': 'Windows Boot Manager (on /dev/nvme0n1p1)'}], 'current': current}
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], return_code, json.dumps(data), '')) as run:
            if self.window._boot_settings_window is None:
                self.window._show_boot_settings()
                self.dialog = self.window._boot_settings_window
                widgets = list(self.walk(self.dialog))
                self.rows = {w.get_title(): w for w in widgets if isinstance(w, self.module.Adw.ComboRow)}
                self.buttons = {w.get_label(): w for w in widgets if isinstance(w, self.module.Gtk.Button)}
            else:
                self.buttons['Retry'].emit('clicked')
            self.assertFalse(self.buttons['Retry'].get_visible())
            self.assertFalse(self.buttons['Apply'].get_sensitive())
            self.settle()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/pkexec', self.module.BOOT_SETTINGS_HELPER, 'list-systems'])

    def test_opening_window_automatically_loads_once(self):
        self.load()
        self.assertFalse(self.buttons['Retry'].get_visible())
        self.assertTrue(self.rows['Default operating system'].get_sensitive())
        display = self.rows['Display mode']
        self.assertEqual([display.get_model().get_string(i) for i in range(3)],
                         ['High resolution (if available)', 'Automatic', 'Large text mode'])
        self.assertEqual(display.get_selected(), 1)
        display_group = next(
            widget for widget in self.walk(self.dialog)
            if isinstance(widget, self.module.Adw.PreferencesGroup)
            and widget.get_title() == 'Boot display'
        )
        self.assertIn('text small on 4K displays', display_group.get_description())
        self.assertIn('native resolution is not guaranteed', display_group.get_description())
        with patch.object(self.module.subprocess, 'run') as run:
            self.window._show_boot_settings()
            run.assert_not_called()
        self.assertIs(self.dialog, self.window._boot_settings_window)

    def test_high_resolution_selection_reaches_the_privileged_helper(self):
        self.load()
        self.rows['Display mode'].set_selected(0)
        self.assertTrue(self.buttons['Apply'].get_sensitive())
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            self.buttons['Apply'].emit('clicked')
            self.settle()
        self.assertEqual(run.call_args.args[0][-1], 'high-resolution')
        self.assertFalse(self.buttons['Apply'].get_sensitive())

    def test_load_select_save_and_later_timeout_edit(self):
        self.load()
        system = self.rows['Default operating system']
        self.assertEqual(system.get_selected(), 0)
        self.assertFalse(self.buttons['Apply'].get_sensitive())
        system.set_selected(1)
        self.assertTrue(self.buttons['Apply'].get_sensitive())
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            self.buttons['Apply'].emit('clicked')
            self.settle()
            self.assertEqual(run.call_args.args[0][-1], 'osprober-efi-1234')
            self.assertFalse(self.buttons['Apply'].get_sensitive())
            self.rows['Boot menu wait time'].set_selected(1)
            self.buttons['Apply'].emit('clicked')
            self.settle()
            self.assertEqual(len(run.call_args.args[0]), 5)

    def test_custom_default_is_preserved_until_explicit_selection(self):
        self.load(current=None)
        system = self.rows['Default operating system']
        self.assertEqual(system.get_selected_item().get_string(), 'Custom setting (unchanged)')
        system.set_selected(2)
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.buttons['Apply'].emit('clicked')
            self.settle()
        self.assertEqual(system.get_model().get_n_items(), 2)
        self.assertEqual(system.get_selected(), 1)
        self.assertFalse(self.buttons['Apply'].get_sensitive())

    def test_cancelled_load_can_retry_and_does_not_disable_other_settings(self):
        self.load(return_code=126)
        self.assertTrue(self.buttons['Retry'].get_sensitive())
        self.assertTrue(self.buttons['Retry'].get_visible())
        self.assertFalse(self.rows['Default operating system'].get_sensitive())
        self.rows['Boot menu wait time'].set_selected(1)
        self.assertTrue(self.buttons['Apply'].get_sensitive())
        self.load()

    def test_failed_save_keeps_selection_available_for_retry(self):
        self.load()
        system = self.rows['Default operating system']
        system.set_selected(1)
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'failed')):
            self.buttons['Apply'].emit('clicked')
            self.settle()
        self.assertEqual(system.get_selected(), 1)
        self.assertTrue(system.get_sensitive())
        self.assertTrue(self.buttons['Apply'].get_sensitive())


if __name__ == '__main__':
    unittest.main()


class RecommendedInstallTests(unittest.TestCase):
    def test_missing_network_editor_and_scanning_app_have_guarded_streaming_installs(self):
        from anduinos_control_panel import app
        from anduinos_control_panel.topics import get_topic
        app.Adw.init()
        application = app.Adw.Application(application_id='com.anduinos.ControlPanel.InstallTest')
        application.register(None)
        start = subprocess.Popen
        for topic_id, package in (("network.advanced", "network-manager-gnome"),
                                  ("hardware.scanners", "simple-scan")):
            for code in (0, 100, 126):
                with self.subTest(topic=topic_id, code=code):
                    commands = []
                    def child(command, **kwargs):
                        commands.append(command)
                        return start([sys.executable, '-u', '-c',
                            "import sys,time; print('Downloading support'); time.sleep(.15); "
                            f"print('Finished', file=sys.stderr); sys.exit({code})"], **kwargs)
                    with patch.object(app.ControlPanelWindow, '_rebuild_categories'), \
                         patch.object(app, '_', side_effect=lambda value: value), \
                         patch.object(app, 'command_available', return_value=False), \
                         patch.object(app.subprocess, 'Popen', side_effect=child):
                        parent = app.ControlPanelWindow(application)
                        operation = None
                        try:
                            with patch.object(parent, '_launch') as launch:
                                topic = get_topic(topic_id)
                                parent._offer_recommended_install(topic)
                                confirmation = next(w for w in app.Gtk.Window.list_toplevels()
                                    if isinstance(w, app.Adw.MessageDialog) and w.get_transient_for() == parent)
                                confirmation.response('install')
                                operation = next(w for w in app.Gtk.Window.list_toplevels()
                                    if isinstance(w, app.Adw.Window) and not isinstance(w, app.Adw.MessageDialog)
                                    and w.get_transient_for() == parent)
                                widgets = list(BootUiTests.walk(operation))
                                close = next(w for w in widgets if isinstance(w, app.Gtk.Button) and w.get_label() == 'Close')
                                self.assertFalse(close.get_sensitive())
                                self.assertTrue(operation.emit('close-request'))
                                self.assertTrue(parent.emit('close-request'))
                                with patch.object(app, 'command_available', return_value=True):
                                    deadline = time.monotonic() + 5
                                    while not close.get_sensitive() and time.monotonic() < deadline:
                                        app.GLib.MainContext.default().iteration(False)
                                        time.sleep(.005)
                                self.assertTrue(close.get_sensitive())
                                self.assertTrue(operation.get_deletable())
                                self.assertFalse(parent.emit('close-request'))
                                self.assertEqual(commands, [['/usr/bin/pkexec', '/usr/bin/apt-get', 'install', '--yes', package]])
                                output = next(w for w in widgets if isinstance(w, app.Gtk.Expander)).get_child().get_child().get_buffer()
                                log = output.get_text(*output.get_bounds(), False)
                                self.assertIn('Downloading support', log)
                                self.assertIn('Finished', log)
                                if code == 126:
                                    self.assertIn('Authentication was cancelled.', log)
                                if code == 0:
                                    launch.assert_called_once_with(list(topic.command))
                                else:
                                    launch.assert_not_called()
                                    self.assertTrue(next(w for w in widgets if isinstance(w, app.Gtk.Expander)).get_expanded())
                        finally:
                            if operation is not None:
                                operation.destroy()
                            parent.destroy()
