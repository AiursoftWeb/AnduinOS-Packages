"""Real widgets: a running package operation cannot be closed or report success early."""
import unittest
import subprocess
import sys
import time
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk
from anduinos_driver_center.operation_ui import OperationWindow
from anduinos_driver_center.app import DriverCenterWindow


class OperationWindowTests(unittest.TestCase):
    def test_failed_process_streams_both_outputs_and_releases_operation(self):
        Gtk.init(); Adw.init()
        app = Adw.Application(application_id='com.anduinos.OperationTest')
        app.register(None)
        start = subprocess.Popen
        # Substitute only the privileged process boundary with a harmless
        # local child; exercise the real streaming worker and GTK callbacks.
        def child(command, **kwargs):
            return start([sys.executable, '-u', '-c',
                "import sys; print('Download started'); print('Connection failed', file=sys.stderr); sys.exit(7)"], **kwargs)
        with patch.object(DriverCenterWindow, 'refresh'), \
             patch('anduinos_driver_center.app.subprocess.Popen', side_effect=child):
            parent = DriverCenterWindow(app)
            try:
                parent._run_action(Gtk.Button(), ['install-xbox'])
                deadline = time.monotonic() + 5
                while parent._operation_running and time.monotonic() < deadline:
                    GLib.MainContext.default().iteration(False)
                self.assertFalse(parent._operation_running)
                operation = next(w for w in Gtk.Window.list_toplevels() if isinstance(w, OperationWindow))
                buffer = operation.log.get_buffer()
                text = buffer.get_text(*buffer.get_bounds(), False)
                self.assertIn('Download started', text)
                self.assertIn('Connection failed', text)
                self.assertLess(operation.progress.get_fraction(), 1.0)
                operation.destroy()
            finally:
                parent.destroy()

    def test_xbox_install_is_visible_and_exclusive_until_the_helper_exits(self):
        Gtk.init(); Adw.init()
        app = Adw.Application(application_id='com.anduinos.XboxOperationTest')
        app.register(None)
        start = subprocess.Popen
        commands = []
        def child(command, **kwargs):
            commands.append(command)
            return start([sys.executable, '-u', '-c',
                "import time; print('Building Xbox module', flush=True); time.sleep(.25); print('Module ready')"], **kwargs)
        with patch.object(DriverCenterWindow, 'refresh'), \
             patch('anduinos_driver_center.app.subprocess.Popen', side_effect=child):
            parent = DriverCenterWindow(app)
            operation = None
            try:
                button = Gtk.Button(label='Install Driver')
                parent._run_action(button, ['install-xbox'])
                operation = next(w for w in Gtk.Window.list_toplevels() if isinstance(w, OperationWindow))
                self.assertTrue(parent.emit('close-request'))
                self.assertTrue(operation.emit('close-request'))
                parent._run_action(Gtk.Button(), ['repair-xbox'])
                deadline = time.monotonic() + 5
                while parent._operation_running and time.monotonic() < deadline:
                    GLib.MainContext.default().iteration(False)
                    time.sleep(.005)
                self.assertFalse(parent._operation_running)
                self.assertFalse(parent.emit('close-request'))
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0][-1], 'install-xbox')
                self.assertTrue(button.get_sensitive())
                self.assertTrue(operation.close_button.get_sensitive())
                self.assertEqual(operation.progress.get_fraction(), 1.0)
                buffer = operation.log.get_buffer()
                self.assertIn('Module ready', buffer.get_text(*buffer.get_bounds(), False))
            finally:
                if operation is not None:
                    operation.destroy()
                parent.destroy()

    def test_running_operation_prevents_close_then_releases_on_failure(self):
        Gtk.init(); Adw.init()
        parent = Adw.Window()
        window = OperationWindow(parent)
        try:
            self.assertTrue(window.emit('close-request'))
            self.assertFalse(window.close_button.get_sensitive())
            window.append('Downloading package\n')
            window.finish(1, 'Connection failed')
            self.assertFalse(window.running)
            self.assertTrue(window.close_button.get_sensitive())
            self.assertLess(window.progress.get_fraction(), 1.0)
            self.assertEqual(window.status.get_label(), 'Connection failed')
            buffer = window.log.get_buffer()
            self.assertIn('Downloading package', buffer.get_text(*buffer.get_bounds(), False))
        finally:
            window.destroy(); parent.destroy()

    def test_long_log_is_bounded_and_success_finishes_progress(self):
        Gtk.init(); Adw.init()
        parent = Adw.Window()
        window = OperationWindow(parent)
        try:
            window.append('x' * 250000 + '\nLatest output\n')
            buffer = window.log.get_buffer()
            self.assertLessEqual(buffer.get_char_count(), 200000)
            self.assertTrue(buffer.get_text(*buffer.get_bounds(), False).endswith('Latest output\n'))
            window.finish(0, 'Done')
            self.assertEqual(window.progress.get_fraction(), 1.0)
        finally:
            window.destroy(); parent.destroy()
