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
        self.labels = []
        self.rows = {}

        def label(**kwargs):
            widget = Mock()
            self.labels.append(widget)
            return widget

        def row(**kwargs):
            widget = Mock()
            self.rows[kwargs.get("title")] = widget
            return widget

        def button(**kwargs):
            widget = Mock()
            self.buttons[kwargs.get("label")] = widget
            return widget

        self.gtk.Button.side_effect = button
        self.gtk.Label.side_effect = label
        self.adw.ActionRow.side_effect = row
        self.action = self.enterContext(patch.object(ui, "run_action"))
        self.inspect = self.enterContext(patch.object(ui, "inspect_secure_boot"))
        self.thread = self.enterContext(patch.object(ui.threading, "Thread"))
        self.firmware = Mock(return_value=(True, ""))

    def page(self, status, *, dkms_available=False, enrolled=True, boot_loader="shim", modules_ready=False,
             setup_mode=False, configuration_present=True, enrollment_pending=False):
        self.buttons.clear()
        self.labels.clear()
        self.rows.clear()
        state = SecureBootState(
            status is SecureBootStatus.ENABLED, True, True, enrolled, "serial",
            dkms_available=dkms_available, status=status, boot_loader=boot_loader,
            setup_mode=setup_mode, configuration_present=configuration_present,
            enrollment_pending=enrollment_pending,
        )
        ui.create_secure_boot_page(
            initial_state=(state, DkmsState(modules=("driver",), untrusted_modules=() if modules_ready else ("driver",))),
            translate=lambda text: "translated:" + text,
            firmware_setup=self.firmware,
        )
        self.action.assert_not_called()
        self.inspect.assert_not_called()
        self.thread.assert_not_called()

    @property
    def warning(self):
        return self.labels[2]

    @property
    def status(self):
        return self.labels[3]

    def test_enrolled_disabled_system_is_ready_without_boot_failure_warning(self):
        self.page(SecureBootStatus.DISABLED, modules_ready=True)
        self.warning.set_visible.assert_called_with(False)
        self.status.set_label.assert_called_with(
            "translated:" + ui._READY_TO_ENABLE + "\ntranslated:" + ui._FIRMWARE_SETUP_INSTRUCTIONS)
        button = self.buttons["translated:Enable Secure Boot"]
        button.set_visible.assert_called_with(True)
        button.set_label.assert_called_with("translated:Enable Secure Boot")
        self.rows["translated:Secure Boot"].set_subtitle.assert_called_with("translated:Secure Boot is disabled")

    def test_setup_mode_reports_firmware_keys_not_missing_mok(self):
        self.page(SecureBootStatus.DISABLED, modules_ready=True, setup_mode=True)
        self.warning.set_visible.assert_called_with(True)
        self.warning.set_label.assert_called_with("translated:" + ui._SETUP_MODE)
        self.status.set_label.assert_called_with(
            "translated:" + ui._READY_TO_ENABLE + "\ntranslated:" + ui._SETUP_MODE_INSTRUCTIONS)
        button = self.buttons["translated:Enable Secure Boot"]
        button.set_visible.assert_called_with(True)
        button.set_label.assert_called_with("translated:Open UEFI Settings")
        self.buttons[None].set_visible.assert_called_with(False)  # No MOK re-enrollment.
        button.connect.call_args.args[1](button)
        self.adw.MessageDialog.new.assert_called_once_with(
            unittest.mock.ANY, "translated:Reboot Required", "translated:" + ui._SETUP_MODE_INSTRUCTIONS)
        self.firmware.assert_not_called()

    def test_direct_grub_boot_still_warns_and_requires_preparation(self):
        self.page(SecureBootStatus.DISABLED, modules_ready=True, boot_loader="grub", setup_mode=True)
        self.warning.set_label.assert_called_with(
            "translated:" + ui._BOOT_WARNING + "\ntranslated:" + ui._SETUP_MODE)
        self.buttons["translated:Enable Secure Boot"].set_visible.assert_called_with(False)
        self.buttons[None].set_visible.assert_called_with(True)

    def test_unsigned_modules_or_missing_signing_config_do_not_request_reenrollment(self):
        for modules_ready, config in ((False, True), (True, False)):
            with self.subTest(modules_ready=modules_ready, configuration=config):
                self.page(SecureBootStatus.DISABLED, modules_ready=modules_ready,
                          configuration_present=config, dkms_available=True)
                self.buttons["translated:Enable Secure Boot"].set_visible.assert_called_with(False)
                self.buttons["translated:Repair Module Signatures"].set_visible.assert_called_with(True)
                self.assertNotIn(ui._PREPARE_FIRST, self.status.set_label.call_args.args[0])

    def test_pending_mok_does_not_claim_ready_to_enable(self):
        self.page(SecureBootStatus.DISABLED, enrolled=False, enrollment_pending=True, modules_ready=True)
        self.buttons["translated:Enable Secure Boot"].set_visible.assert_called_with(False)
        self.assertIn("waiting for enrollment", self.status.set_label.call_args.args[0])

    def test_enabled_trusted_system_keeps_success_status(self):
        for loader in ("shim", "unknown", "grub"):
            with self.subTest(boot_loader=loader):
                self.page(SecureBootStatus.ENABLED, modules_ready=True, boot_loader=loader)
                self.warning.set_visible.assert_called_with(False)
                for label in (None, "translated:Enable Secure Boot",
                              "translated:Repair Module Signatures", "translated:Reboot",
                              "translated:  Check Again  "):
                    self.buttons[label].set_visible.assert_called_with(False)
                self.status.set_label.assert_called_with(
                    "translated:System Trust Established. Third-party drivers will load securely.")

    def test_enabled_system_with_unknown_loader_still_offers_required_trust_actions(self):
        self.page(SecureBootStatus.ENABLED, enrolled=False, boot_loader="unknown", modules_ready=True)
        self.buttons[None].set_visible.assert_called_with(True)
        for modules_ready, config in ((False, True), (True, False)):
            with self.subTest(modules_ready=modules_ready, configuration=config):
                self.page(SecureBootStatus.ENABLED, boot_loader="unknown", modules_ready=modules_ready,
                          configuration_present=config, dkms_available=True)
                self.buttons[None].set_visible.assert_called_with(False)
                self.buttons["translated:Repair Module Signatures"].set_visible.assert_called_with(True)

    def test_disabled_system_with_unknown_loader_still_requires_boot_preparation(self):
        self.page(SecureBootStatus.DISABLED, boot_loader="unknown", modules_ready=True)
        self.buttons[None].set_visible.assert_called_with(True)
        self.buttons["translated:Enable Secure Boot"].set_visible.assert_called_with(False)

    def test_only_supported_enabled_state_with_dkms_offers_module_repair(self):
        for status in SecureBootStatus:
            for available in (False, True):
                with self.subTest(status=status, dkms_available=available):
                    self.page(status, dkms_available=available)
                    repair = self.buttons["translated:Repair Module Signatures"]
                    repair.set_visible.assert_called_with(
                        status in {SecureBootStatus.ENABLED, SecureBootStatus.DISABLED} and available
                    )
                    self.buttons["translated:Enable Secure Boot"].set_visible.assert_called_with(
                        False  # Unsigned modules must be repaired first.
                    )

    def test_firmware_restart_requires_explicit_confirmation(self):
        self.page(SecureBootStatus.DISABLED, modules_ready=True)
        button = self.buttons["translated:Enable Secure Boot"]
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

    def test_enable_is_blocked_until_enrolled_and_booting_shim(self):
        for enrolled, loader in ((False, "shim"), (True, "grub"), (True, "unknown")):
            self.page(SecureBootStatus.DISABLED, enrolled=enrolled, boot_loader=loader, modules_ready=True)
            button = self.buttons["translated:Enable Secure Boot"]
            button.set_visible.assert_called_with(False)
            button.connect.call_args.args[1](button)
            self.firmware.assert_not_called()
            self.adw.MessageDialog.new.assert_not_called()


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
