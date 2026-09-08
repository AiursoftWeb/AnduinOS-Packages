from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_secureboot.ui import restart_to_firmware_settings  # noqa: E402
from anduinos_secureboot import ui
from anduinos_secureboot.model import DkmsState, SecureBootState, SecureBootStatus


class TrustPageTests(unittest.TestCase):
    def setUp(self):
        # Mock the rendering boundary, not the page logic or signal callbacks.
        # No display, host inspection, privileged action or reboot is needed.
        self.gtk = self.enterContext(patch.object(ui, "Gtk"))
        self.adw = self.enterContext(patch.object(ui, "Adw"))
        self.buttons = {}

        def button(**kwargs):
            widget = Mock()
            self.buttons[kwargs.get("label")] = widget
            return widget

        self.gtk.Button.side_effect = button
        self.action = self.enterContext(patch.object(ui, "run_action"))
        self.inspect = self.enterContext(patch.object(ui, "inspect_secure_boot"))
        self.thread = self.enterContext(patch.object(ui.threading, "Thread"))
        self.firmware = Mock(return_value=(True, ""))

    def page(self, status, *, dkms_available=False):
        state = SecureBootState(
            status is SecureBootStatus.ENABLED, True, True, True, "serial",
            dkms_available=dkms_available, status=status,
        )
        ui.create_secure_boot_page(
            initial_state=(state, DkmsState(modules=("driver",), untrusted_modules=("driver",))),
            translate=lambda text: "translated:" + text,
            firmware_setup=self.firmware,
        )
        self.action.assert_not_called()
        self.inspect.assert_not_called()
        self.thread.assert_not_called()

    def test_only_supported_enabled_state_with_dkms_offers_module_repair(self):
        for status in SecureBootStatus:
            for available in (False, True):
                with self.subTest(status=status, dkms_available=available):
                    self.page(status, dkms_available=available)
                    repair = self.buttons["translated:Repair Module Signatures"]
                    repair.set_visible.assert_called_with(
                        status is SecureBootStatus.ENABLED and available
                    )
                    self.buttons["translated:Resolve"].set_visible.assert_called_with(
                        status is SecureBootStatus.DISABLED
                    )

    def test_firmware_restart_requires_explicit_confirmation(self):
        self.page(SecureBootStatus.DISABLED)
        button = self.buttons["translated:Resolve"]
        signal, clicked = button.connect.call_args.args
        self.assertEqual(signal, "clicked")
        clicked(button)
        dialog = self.adw.MessageDialog.new.return_value
        dialog.set_default_response.assert_called_once_with("cancel")
        dialog.set_close_response.assert_called_once_with("cancel")
        signal, response = dialog.connect.call_args.args
        self.assertEqual(signal, "response")
        for name in ("cancel", "close", "unexpected"):
            response(dialog, name)
            self.firmware.assert_not_called()
            self.thread.assert_not_called()
        response(dialog, "reboot")
        self.thread.assert_called_once()
        self.thread.return_value.start.assert_called_once()
        self.thread.call_args.kwargs["target"]()
        self.firmware.assert_called_once_with()


class FirmwareSettingsTests(unittest.TestCase):
    def test_restart_uses_the_fixed_systemd_firmware_command(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        self.assertEqual(restart_to_firmware_settings(runner), (True, ""))
        self.assertEqual(
            calls[0][0],
            ["systemctl", "reboot", "--firmware-setup"],
        )
        self.assertFalse(calls[0][1]["check"])
        self.assertEqual(calls[0][1]["timeout"], 10)

    def test_restart_returns_firmware_errors_to_the_ui(self):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, "", "not supported")

        self.assertEqual(
            restart_to_firmware_settings(runner),
            (False, "not supported"),
        )


if __name__ == "__main__":
    unittest.main()
