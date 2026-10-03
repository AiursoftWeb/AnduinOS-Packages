"""Save current installer output through the native save dialog."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pages
from gi.repository import Adw, Gio, GLib, Gtk


class SaveLogTests(unittest.TestCase):
    def setUp(self):
        self.window = Adw.Window()
        self.addCleanup(self.window.destroy)
        self.button = Gtk.Button()
        self.window.set_content(self.button)
        self.buffer = Gtk.TextBuffer()
        self.buffer.set_text("first line\n")

    def wait_for_notification(self, notification):
        deadline = time.monotonic() + 3
        while not notification.called and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertTrue(notification.called)

    def test_save_uses_selected_path_and_confirmation_time_snapshot(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch("pages.Gtk.FileDialog") as chooser,
              patch("pages.Adw.MessageDialog") as notice):
            destination = Path(directory) / "chosen.log"
            chooser.return_value.save_finish.return_value = Gio.File.new_for_path(
                str(destination))
            pages._save_log(self.buffer, self.button, "en_US")
            self.assertFalse(destination.exists())
            self.assertEqual(chooser.call_args.kwargs["initial_name"],
                             "anduinos-install.log")
            self.assertIs(chooser.return_value.save.call_args.args[0], self.window)
            self.buffer.insert(self.buffer.get_end_iter(), "保存前\n")
            callback = chooser.return_value.save.call_args.args[2]
            callback(chooser.return_value, object())
            self.buffer.insert(self.buffer.get_end_iter(), "after confirmation\n")
            self.wait_for_notification(notice)
            self.assertEqual(destination.read_text(), "first line\n保存前\n")
            self.assertEqual(notice.call_args.kwargs["heading"], "Saved")
            self.assertEqual(notice.call_args.kwargs["body"], str(destination))

    def test_cancel_does_not_show_error(self):
        with (patch("pages.Gtk.FileDialog") as chooser,
              patch("pages.Adw.MessageDialog") as notice):
            chooser.return_value.save_finish.side_effect = GLib.Error.new_literal(
                Gtk.DialogError.quark(), "Dismissed", Gtk.DialogError.DISMISSED)
            pages._save_log(self.buffer, self.button, "en_US")
            chooser.return_value.save.call_args.args[2](chooser.return_value, object())
            notice.assert_not_called()

    def test_write_failure_is_visible(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch("pages.Gtk.FileDialog") as chooser,
              patch("pages.Adw.MessageDialog") as notice):
            # A nonexistent parent reliably fails even when tests run as root.
            destination = Path(directory) / "missing" / "chosen.log"
            chooser.return_value.save_finish.return_value = Gio.File.new_for_path(
                str(destination))
            pages._save_log(self.buffer, self.button, "en_US")
            chooser.return_value.save.call_args.args[2](chooser.return_value, object())
            self.wait_for_notification(notice)
            self.assertEqual(notice.call_args.kwargs["heading"], "Save Log")
            self.assertTrue(notice.call_args.kwargs["body"])
            self.assertFalse(destination.exists())
