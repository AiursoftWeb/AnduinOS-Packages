import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anduinos_control_panel import app


class PackageTests(unittest.TestCase):
    def test_python_sources_compile_without_cache_files(self):
        for source in [
            ROOT / "src/anduinos-control-panel",
            ROOT / "src/anduinos-control-panel-search-provider",
            *sorted((ROOT / "src/anduinos_control_panel").glob("*.py")),
        ]:
            compile(source.read_text(), str(source), "exec")

    def test_polkit_authorizes_the_helper_used_by_the_client_without_gui_environment(self):
        policy = ET.parse(ROOT / "data/com.anduinos.ControlPanel.policy")
        paths = {
            node.text.strip()
            for node in policy.findall(".//annotate[@key='org.freedesktop.policykit.exec.path']")
        }
        self.assertEqual(paths, {app.BOOT_SETTINGS_HELPER})
        self.assertFalse(policy.findall(".//annotate[@key='org.freedesktop.policykit.exec.allow_gui']"))


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.window = Mock()
        self.enterContext(patch.object(app, "_", side_effect=lambda text: text))

    def test_launch_reports_failed_process_output_but_not_success(self):
        for successful, stdout, stderr, detail in (
            (True, "", "harmless warning", None),
            (False, "output details", "failure details", "failure details"),
            (False, "output details", "", "output details"),
            (False, "", "", "The application exited before it could be opened."),
        ):
            with self.subTest(successful=successful, stderr=stderr):
                self.window.reset_mock()
                process = Mock()
                process.get_successful.return_value = successful
                process.communicate_utf8_finish.return_value = (True, stdout, stderr)
                with patch.object(app.Gio.Subprocess, "new", return_value=process):
                    app.ControlPanelWindow._launch(self.window, ["test-app", "literal; argument"])
                callback = process.communicate_utf8_async.call_args.args[2]
                callback(process, Mock())
                if detail is None:
                    self.window._show_error.assert_not_called()
                else:
                    self.window._show_error.assert_called_once_with("Could not open this setting", detail)

    def test_launch_reports_spawn_and_async_transport_errors(self):
        error = app.GLib.Error("permission denied")
        for spawn_error in (False, True):
            with self.subTest(spawn_error=spawn_error):
                self.window.reset_mock()
                process = Mock()
                process.communicate_utf8_finish.side_effect = error
                with patch.object(app.Gio.Subprocess, "new", return_value=process,
                                  side_effect=error if spawn_error else None):
                    app.ControlPanelWindow._launch(self.window, ["test-app"])
                if not spawn_error:
                    process.communicate_utf8_async.call_args.args[2](process, Mock())
                self.assertIn("permission denied", self.window._show_error.call_args.args[1])

    def test_missing_optional_application_offers_install_instead_of_launching(self):
        topic = app.get_topic("hardware.scanners")
        for installed in (False, True):
            with self.subTest(installed=installed):
                self.window.reset_mock()
                with patch.object(app, "command_available", return_value=installed):
                    app.ControlPanelWindow._activate_topic(self.window, topic.identifier)
                if installed:
                    self.window._launch.assert_called_once_with(list(topic.command))
                    self.window._offer_recommended_install.assert_not_called()
                else:
                    self.window._offer_recommended_install.assert_called_once_with(topic)
                    self.window._launch.assert_not_called()

    def test_cancelling_recommended_install_never_starts_a_command(self):
        with patch.object(app.Adw, "MessageDialog") as dialog_type:
            app.ControlPanelWindow._offer_recommended_install(
                self.window, app.get_topic("hardware.scanners")
            )
        dialog = dialog_type.return_value
        response = dialog.connect.call_args.args[1]
        with (
            patch.object(app.threading, "Thread") as thread,
            patch.object(app.subprocess, "run", return_value=Mock(
                returncode=126, stderr="", stdout="",
            )) as run,
            patch.object(app.Adw.Toast, "new"),
            patch.object(app.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)),
        ):
            for name in ("cancel", "close", "unexpected"):
                response(dialog, name)
            thread.assert_not_called()
            run.assert_not_called()
            response(dialog, "install")
            thread.return_value.start.assert_called_once()
            thread.call_args.kwargs["target"]()
        self.assertEqual(run.call_args.args[0], [
            "/usr/bin/pkexec", "/usr/bin/apt-get", "install", "--yes", "simple-scan",
        ])
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.window._launch.assert_not_called()
        self.window._show_error.assert_called_once_with("Installation failed", "Authentication was cancelled.")


class StreamingCommandTests(unittest.TestCase):
    def setUp(self):
        self.window = Mock()
        self.success, self.failure = Mock(), Mock()
        self.buffer, self.output = Mock(), Mock()
        self.enterContext(patch.object(app, "_", side_effect=lambda text: text))
        self.enterContext(patch.object(app.threading, "Thread",
                                      side_effect=lambda *, target, **kw: Mock(start=target)))
        self.enterContext(patch.object(app.GLib, "idle_add",
                                      side_effect=lambda function, *args: function(*args)))

    def execute(self, commands):
        app.ControlPanelWindow._run_streaming_commands(
            self.window, commands, self.buffer, self.output, self.success, self.failure
        )

    def test_failed_first_command_streams_details_and_prevents_later_changes(self):
        process = Mock(stdout=io.StringIO("download started\ndependency unavailable\n"))
        process.wait.return_value = 100
        commands = [["test-package-manager", "install"], ["must-not-run"]]
        with patch.object(app.subprocess, "Popen", return_value=process) as popen:
            self.execute(commands)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], commands[0])
        self.assertFalse(popen.call_args.kwargs.get("shell", False))
        streamed = "".join(call.args[-1] for call in self.window._append_package_output.call_args_list)
        self.assertIn("dependency unavailable", streamed)
        self.success.assert_not_called()
        self.failure.assert_called_once()
        self.assertIn("100", self.failure.call_args.args[0])

    def test_success_requires_every_command_to_finish_and_preserves_literal_arguments(self):
        commands = [["test-app", "$(do-not-expand); x"], ["second-app"]]
        processes = [Mock(stdout=io.StringIO("done\n")), Mock(stdout=io.StringIO(""))]
        for process in processes:
            process.wait.return_value = 0
        with patch.object(app.subprocess, "Popen", side_effect=processes) as popen:
            self.execute(commands)
        self.assertEqual([call.args[0] for call in popen.call_args_list], commands)
        self.assertTrue(all(not call.kwargs.get("shell", False) for call in popen.call_args_list))
        for process in processes:
            process.wait.assert_called_once()
        self.success.assert_called_once_with()
        self.failure.assert_not_called()

    def test_spawn_failure_is_reported_without_claiming_success(self):
        with patch.object(app.subprocess, "Popen", side_effect=OSError("cannot start")):
            self.execute([["test-app"], ["must-not-run"]])
        self.failure.assert_called_once_with("cannot start")
        self.success.assert_not_called()


if __name__ == "__main__":
    unittest.main()
