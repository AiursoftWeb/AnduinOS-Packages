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
            patch.object(app, 'read_grub_display_mode', return_value='native'),
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
        with patch.object(self.module.subprocess, 'run') as run:
            self.window._show_boot_settings()
            run.assert_not_called()
        self.assertIs(self.dialog, self.window._boot_settings_window)

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
