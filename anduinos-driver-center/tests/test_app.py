from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_driver_center.app import DriverCenterWindow, _command_output_summary  # noqa: E402
from anduinos_driver_center import app


class AppTests(unittest.TestCase):
    def test_command_line_opens_requested_page_in_new_or_existing_window(self):
        for existing in (False, True):
            for arguments in (["--page", "secure-boot"], ["--page=secure-boot"]):
                with self.subTest(existing=existing, arguments=arguments):
                    application, command_line = Mock(), Mock()
                    command_line.get_arguments.return_value = ["driver-center", *arguments]
                    window = Mock()
                    application.get_active_window.return_value = window if existing else None
                    with patch.object(app, "DriverCenterWindow", return_value=window) as constructor:
                        self.assertEqual(app.DriverCenterApplication.do_command_line(application, command_line), 0)
                    if existing:
                        constructor.assert_not_called()
                        window._select_page.assert_called_once_with("secure-boot")
                    else:
                        constructor.assert_called_once_with(application, initial_page="secure-boot")
                    window.present.assert_called_once()

    def test_unknown_page_or_argument_does_not_open_a_window(self):
        for arguments in (["--page"], ["--page=unknown"], ["--unexpected"]):
            with self.subTest(arguments=arguments):
                application, command_line = Mock(), Mock()
                command_line.get_arguments.return_value = ["driver-center", *arguments]
                with patch.object(app, "DriverCenterWindow") as constructor:
                    self.assertNotEqual(app.DriverCenterApplication.do_command_line(application, command_line), 0)
                constructor.assert_not_called()
                application.get_active_window.assert_not_called()
                command_line.printerr.assert_called_once()

    def run_action_result(self, result):
        window = Mock()
        button = Mock()
        with (
            patch("anduinos_driver_center.app.subprocess.run", return_value=result),
            patch("anduinos_driver_center.app.threading.Thread") as thread,
            patch("anduinos_driver_center.app.GLib.idle_add") as idle,
        ):
            thread.side_effect = lambda *, target, **kwargs: Mock(start=target)
            DriverCenterWindow._run_action(window, button, ["repair-nvidia", "nvidia-driver-610-open"])
        return idle.call_args.args[4]

    def test_failure_preserves_stderr_and_stdout(self):
        stdout = "+ apt-get install -y --reinstall nvidia-driver-610-open\n"
        stderr = "E: Unable to correct problems, you have held broken packages.\nDriver operation failed: apt-get exited with status 100\n"
        message = self.run_action_result(subprocess.CompletedProcess([], 1, stdout, stderr))
        self.assertEqual(message, stderr.strip() + "\n\n" + stdout.strip())

    def test_failure_preserves_multiline_stdout_without_stderr(self):
        stdout = "The following packages have unmet dependencies:\n nvidia-driver: Depends: unavailable-package\n"
        message = self.run_action_result(subprocess.CompletedProcess([], 1, stdout, ""))
        self.assertEqual(message, stdout.strip())

    def test_failure_preserves_stderr_without_stdout(self):
        message = self.run_action_result(subprocess.CompletedProcess([], 1, "", "Authorization failed\n"))
        self.assertEqual(message, "Authorization failed")

    def test_empty_failure_uses_unknown_error_dialog(self):
        message = self.run_action_result(subprocess.CompletedProcess([], 1, "", ""))
        window = Mock()
        button = Mock()
        DriverCenterWindow._action_done(window, button, "Apply", 1, message)
        window._action_error.assert_called_once_with("unknown error")
        window.refresh.assert_not_called()
        button.set_sensitive.assert_called_once_with(True)

    def test_success_still_uses_last_output_line(self):
        message = self.run_action_result(subprocess.CompletedProcess([], 0, "command output\nDone\n", "warning"))
        self.assertEqual(message, "Done")

    def test_failure_dialog_has_scrollable_copyable_details(self):
        with (
            patch("anduinos_driver_center.app.Adw.MessageDialog") as dialog_type,
            patch("anduinos_driver_center.app.Gtk.TextView") as text_view_type,
            patch("anduinos_driver_center.app._scrolled_window") as scrolled_type,
        ):
            message = "E: Package unavailable\n" + "package details\n" * 200
            DriverCenterWindow._action_error(None, message)
        dialog = dialog_type.return_value
        scroll = dialog.set_extra_child.call_args.args[0]
        self.assertIs(scroll, scrolled_type.return_value)
        details = text_view_type.return_value
        scroll.set_child.assert_called_once_with(details)
        details.get_buffer.return_value.set_text.assert_called_once_with(message)
        self.assertFalse(text_view_type.call_args.kwargs["editable"])
        self.assertFalse(text_view_type.call_args.kwargs["cursor_visible"])
        self.assertEqual(scrolled_type.call_args.kwargs["max_content_height"], 360)
        self.assertNotIn(message, dialog_type.call_args.kwargs["heading"])
        dialog.present.assert_called_once()

    def test_recommended_install_uses_the_ubuntu_drivers_conclusion(self):
        output = """+ apt-get update
Reading package lists... Done
+ ubuntu-drivers install
All the available drivers are already installed.
Driver operation completed successfully.
"""
        self.assertEqual(
            _command_output_summary(output, "+ ubuntu-drivers install"),
            "All the available drivers are already installed.",
        )

    def test_command_summary_requires_the_requested_command_marker(self):
        self.assertIsNone(
            _command_output_summary(
                "Driver operation completed successfully.",
                "+ ubuntu-drivers install",
            )
        )


if __name__ == "__main__":
    unittest.main()
