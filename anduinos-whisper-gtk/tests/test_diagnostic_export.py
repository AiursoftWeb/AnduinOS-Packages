import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_whisper_gtk.dbus import VoiceServiceClient
from anduinos_whisper_gtk.app import SettingsWindow
from gi.repository import Gio, GLib


class DiagnosticExportTests(unittest.TestCase):
    def test_export_is_async_readonly_and_revalidates_privacy(self):
        client = VoiceServiceClient.__new__(VoiceServiceClient)
        client.connection = Mock()
        client.connection.call_finish.return_value.unpack.return_value = (
            json.dumps({"schema_version": 1, "measurements": [{"inference_ms": 123,
                         "text": "SECRET", "stderr": "SECRET"}]}),)
        reports = []
        client.diagnostics(reports.append)
        args = client.connection.call.call_args.args
        self.assertEqual(args[3], "GetDiagnostics")
        self.assertEqual(args[6], Gio.DBusCallFlags.NO_AUTO_START)
        self.assertEqual(args[7], 3000)
        args[9](client.connection, Mock(), None)
        self.assertEqual(len(reports), 1)
        self.assertNotIn("SECRET", reports[0])
        self.assertEqual(json.loads(reports[0])["measurements"], [{"inference_ms": 123}])

    def test_invalid_report_is_not_offered_for_saving(self):
        client = VoiceServiceClient.__new__(VoiceServiceClient)
        client.connection = Mock()
        client.connection.call_finish.return_value.unpack.return_value = ("invalid",)
        callback = Mock()
        client.diagnostics(callback)
        client.connection.call.call_args.args[9](client.connection, Mock(), None)
        callback.assert_called_once_with(None)

    def test_save_occurs_only_after_destination_selection_and_is_private(self):
        window = Mock()
        dialog, destination = Mock(), Mock()
        dialog.save_finish.return_value = destination
        report = '{"schema_version":1,"measurements":[]}'
        SettingsWindow._diagnostics_file_selected(window, dialog, Mock(), report)
        args = destination.replace_contents_async.call_args.args
        self.assertEqual(args[0], report.encode())
        self.assertTrue(args[3] & Gio.FileCreateFlags.PRIVATE)
        self.assertTrue(args[3] & Gio.FileCreateFlags.REPLACE_DESTINATION)
        self.assertEqual(args[5], window._diagnostics_saved)

    def test_dismissing_save_chooser_does_not_write(self):
        window, dialog = Mock(), Mock()
        dialog.save_finish.side_effect = GLib.Error("dismissed")
        SettingsWindow._diagnostics_file_selected(window, dialog, Mock(), "report")
        window._diagnostics_saved.assert_not_called()
