"""Exercise the real page callbacks; replace GTK and hardware at their boundaries."""
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_secureboot import ui
from anduinos_secureboot.decision import Facts, NextBootEvidence
from anduinos_secureboot.identity import IdentityStatus
from anduinos_secureboot.model import DkmsState, GrubState, ModuleState, SecureBootState, SecureBootStatus


class TrustPageTests(unittest.TestCase):
    def setUp(self):
        self.gtk = self.enterContext(patch.object(ui, "Gtk"))
        self.gtk.Box.return_value.get_root.return_value.get_width.return_value = 900
        self.adw = self.enterContext(patch.object(ui, "Adw"))
        self.tutorial = self.enterContext(patch.object(ui, "EnrollmentTutorial", create=True))
        self.rows, self.labels, self.all_buttons = {}, [], []
        def row(**kwargs):
            widget = Mock()
            widget.get_title.return_value = kwargs['title']
            self.rows[kwargs['title']] = widget
            return widget
        def label(**kwargs):
            widget = Mock()
            self.labels.append(widget)
            return widget
        def button(**kwargs):
            widget = Mock()
            self.all_buttons.append(widget)
            return widget
        self.adw.ActionRow.side_effect = row
        self.gtk.Label.side_effect = label
        self.gtk.Button.side_effect = button
        self.action = self.enterContext(patch.object(ui, "run_action"))
        self.action.return_value = (0, {"steps": {}})
        self.collect = self.enterContext(patch.object(ui, "collect_facts"))
        self.thread = self.enterContext(patch.object(ui.threading, "Thread"))
        self.firmware = Mock(return_value=(True, ""))
        self.reboot = Mock()
        self.idle = self.enterContext(patch.object(ui.GLib, "idle_add", side_effect=lambda f, *a: f(*a)))
        self.enterContext(patch.object(ui.GLib, "timeout_add", side_effect=lambda _ms, f: f()))
        self.identity = IdentityStatus(purpose="unified", enrollment="enrolled", key_present=True,
                                       key_valid=True, managed=True, defaults_ready=True)
        self.boot = GrubState(installed=True, signature="valid", enrollment="enrolled", deployment="ready", active_chain="trusted")

    def evidence(self, **changes):
        values = dict(loader_source="anduinos", loader_path=r"\EFI\AnduinOS\shimx64.efi",
                      kernel_release="fixture-next-kernel", firmware_trusted=True,
                      loader_trusted=True, kernel_trusted=True, modules_trusted=True)
        values.update(changes)
        return NextBootEvidence(**values)

    def page(self, status=SecureBootStatus.ENABLED, *, signing_identity=None, grub=None,
             modules_ready=True, next_boot=None, setup_mode=False, **changes):
        state = SecureBootState(enabled=status is SecureBootStatus.ENABLED, status=status,
            key_present=True, certificate_present=True, enrolled=True, certificate_serial="old-signer",
            boot_loader="shim", setup_mode=setup_mode, dkms_available=True, headers_available=True,
            signing_identity=signing_identity or self.identity, grub=self.boot if grub is None else grub)
        state = replace(state, **changes)
        drivers = DkmsState(modules=("driver.ko",),
            trusted_modules=("driver.ko",) if modules_ready else (),
            untrusted_modules=() if modules_ready else ("driver.ko",),
            details=(ModuleState("driver.ko", "/fixture/driver.ko", "old-signer" if modules_ready else None,
                                 modules_ready, False),))
        self.facts = Facts(state, drivers, drivers_checked=True, next_boot=next_boot)
        self.collect.return_value = self.facts
        ui.create_secure_boot_page(initial_facts=self.facts, translate=lambda t: "translated:" + t,
                                   firmware_setup=self.firmware, reboot=self.reboot)
        self.collect.assert_not_called()
        self.action.assert_not_called()
        self.thread.assert_not_called()

    def row(self, name):
        return self.rows['translated:' + name]

    def button(self, name):
        return self.row(name).add_suffix.call_args_list[0].args[0]

    def click(self, name):
        decision = ui.decide(self.facts)
        result = decision.rows[list(self.rows).index('translated:' + name)]
        button = (self.all_buttons[-2] if result.id == decision.next_row
                  and result.action and result.action.id != 'refresh' else self.button(name))
        button.connect.call_args.args[1](button)
        if button is not self.all_buttons[-2] and result.action and result.action.allowed:
            self.respond('action')

    def respond(self, response):
        dialog = self.adw.MessageDialog.new.return_value
        dialog.connect.call_args.args[1](dialog, response)

    def worker(self):
        self.thread.call_args.kwargs['target']()

    def test_uninstalled_bootloader_recommends_installation_in_the_row_and_next_step(self):
        self.page(grub=GrubState())
        row = self.row('AnduinOS Bootloader')
        self.assertIn('recommended', row.set_subtitle.call_args.args[0])
        row.add_suffix.call_args_list[0].args[0].set_child.call_args.args[0].set_from_icon_name.assert_called_with(
            'dialog-warning-symbolic')
        self.assertIn('recommended', self.labels[-1].set_label.call_args.args[0])
        self.click('AnduinOS Bootloader')
        self.action.assert_not_called()
        self.assertIn('Install AnduinOS Bootloader', self.adw.MessageDialog.new.call_args.args[1])

    def test_prepared_system_allows_confirming_firmware_restart_without_next_boot_data(self):
        self.page(SecureBootStatus.DISABLED)
        self.click('Secure Boot')
        self.firmware.assert_not_called()
        self.respond('reboot')
        self.worker()
        self.firmware.assert_called_once_with()
        self.action.assert_not_called()

    def test_prepared_disabled_page_agrees_with_firmware_button_and_confirmation(self):
        self.page(SecureBootStatus.DISABLED)
        self.button('Secure Boot').set_can_target.assert_called_with(True)
        self.assertIn('Preparation checks passed', self.labels[-1].set_label.call_args.args[0])
        self.assertIn('still disabled', self.row('Secure Boot').set_subtitle.call_args.args[0])
        self.click('Secure Boot')
        dialog = self.adw.MessageDialog.new.return_value
        self.assertIn(unittest.mock.call('action', 'translated:Open Firmware Settings'),
                      dialog.add_response.call_args_list)
        dialog.set_default_response.assert_called_with('cancel')
        body = self.adw.MessageDialog.new.call_args.args[2]
        self.assertIn('enable Secure Boot', body)
        self.assertNotIn('Do not enable', body)
        self.thread.assert_not_called()
        self.firmware.assert_not_called()

    def test_firmware_restart_rechecks_preparation_after_confirmation(self):
        self.page(SecureBootStatus.DISABLED)
        self.click('Secure Boot')
        self.collect.return_value = replace(self.facts,
            dkms=DkmsState(modules=('driver.ko',), untrusted_modules=('driver.ko',)))
        self.respond('reboot')
        self.worker()
        self.firmware.assert_not_called()
        self.assertIn('trusted signature', self.adw.MessageDialog.new.call_args.args[2])

    def test_setup_mode_reports_platform_keys_not_missing_mok(self):
        self.page(SecureBootStatus.DISABLED, setup_mode=True)
        self.click('Secure Boot')
        self.assertIn('Platform keys', self.adw.MessageDialog.new.call_args.args[2])
        self.action.assert_not_called()
        self.thread.assert_not_called()

    def test_unknown_firmware_is_a_check_action_not_a_reboot(self):
        self.page(SecureBootStatus.UNKNOWN)
        self.click('Secure Boot')
        self.thread.assert_called_once()
        self.action.assert_not_called()
        self.firmware.assert_not_called()

    def test_bios_keeps_all_rows_visible_and_offers_explanation(self):
        self.page(SecureBootStatus.UNSUPPORTED)
        self.assertEqual(len(self.rows), 6)
        for row in self.rows.values():
            row.set_visible.assert_called_with(True)
        self.click('AnduinOS Bootloader')
        self.action.assert_not_called()
        self.assertTrue(self.adw.MessageDialog.new.called)

    def test_healthy_page_offers_no_repairs(self):
        self.page(next_boot=self.evidence())
        for name in ['Secure Boot', 'Local MOK Certificate', 'Certificate Signing Purpose',
                     'Certificate Enrollment', 'Third-party Drivers', 'AnduinOS Bootloader']:
            self.button(name).set_can_target.assert_called_with(False)
        self.assertIn('No repairs', self.labels[-1].set_label.call_args.args[0])

    def test_unsigned_own_grub_does_not_offer_enable_secure_boot(self):
        self.page(SecureBootStatus.DISABLED, grub=replace(self.boot, signature='missing'),
                  next_boot=self.evidence(loader_trusted=False))
        self.click('Secure Boot')
        self.thread.assert_not_called()
        self.firmware.assert_not_called()
        self.assertIn('needs signing', self.adw.MessageDialog.new.call_args.args[2])

    def test_undeployed_own_grub_does_not_offer_enable_secure_boot(self):
        self.page(SecureBootStatus.DISABLED, grub=replace(self.boot, deployment='pending'),
                  next_boot=self.evidence(loader_trusted=None))
        self.click('Secure Boot')
        self.thread.assert_not_called()
        self.firmware.assert_not_called()

    def test_blocked_bootloader_button_still_allows_asking_what_to_do(self):
        self.page(signing_identity=replace(self.identity, purpose='module-only'), grub=GrubState())
        self.button('AnduinOS Bootloader').set_can_target.assert_called_with(True)
        self.button('AnduinOS Bootloader').set_sensitive.assert_called_with(True)
        self.click('AnduinOS Bootloader')
        self.assertIn('both the bootloader and drivers', self.adw.MessageDialog.new.call_args.args[2])
        self.action.assert_not_called()

    def test_blocked_bootloader_click_explains_instead_of_confirming_installation(self):
        self.page(signing_identity=replace(self.identity, enrollment='pending', defaults_ready=False),
                  grub=GrubState())
        self.click('AnduinOS Bootloader')
        dialog = self.adw.MessageDialog.new.return_value
        responses = [call.args[0] for call in dialog.add_response.call_args_list]
        self.assertNotIn('continue', responses)
        self.assertIn('enroll', self.adw.MessageDialog.new.call_args.args[2].lower())
        self.action.assert_not_called()
        self.thread.assert_not_called()

    def test_warning_details_offer_the_actual_prerequisite(self):
        self.page(signing_identity=replace(self.identity, enrollment='pending'), grub=GrubState())
        self.click('AnduinOS Bootloader')
        self.respond('action')
        self.assertIn('Restart Required', self.adw.MessageDialog.new.call_args.args[1])
        self.reboot.assert_not_called()
        self.action.assert_not_called()

    def test_enrollment_action_is_the_single_next_step(self):
        self.page(signing_identity=replace(self.identity, enrollment='absent'))
        self.all_buttons[-2].set_label.assert_called_with('translated:Submit Enrollment Request')
        self.button('Certificate Enrollment').set_can_target.assert_called_with(True)
        self.all_buttons[-2].set_visible.assert_called_with(True)

    def test_pending_enrollment_reboot_is_the_single_next_step(self):
        self.page(signing_identity=replace(self.identity, enrollment='pending'))
        self.all_buttons[-2].set_label.assert_called_with('translated:Restart to Enroll Certificate')
        self.button('Certificate Enrollment').set_can_target.assert_called_with(True)
        self.all_buttons[-2].set_visible.assert_called_with(True)

    def test_pending_enrollment_confirmation_contains_the_offline_tutorial(self):
        self.page(signing_identity=replace(self.identity, enrollment='pending'))
        self.click('Certificate Enrollment')
        self.tutorial.assert_called_once()
        dialog = self.adw.MessageDialog.new.return_value
        dialog.set_extra_child.assert_called_once_with(self.tutorial.return_value)
        dialog.set_default_response.assert_called_with('cancel')
        self.reboot.assert_not_called()

    def test_successfully_queued_enrollment_shows_tutorial_without_restarting(self):
        self.page(signing_identity=replace(self.identity, enrollment='absent'))
        self.action.return_value = (0, {'reboot_required': True})
        self.click('Certificate Enrollment')
        self.respond('continue')
        self.tutorial.assert_not_called()  # Request has not succeeded yet.
        self.worker()
        self.tutorial.assert_called_once()
        self.adw.MessageDialog.new.return_value.set_extra_child.assert_called_with(
            self.tutorial.return_value)
        self.reboot.assert_not_called()

    def test_failed_or_unnecessary_enrollment_does_not_play_the_tutorial(self):
        self.page(signing_identity=replace(self.identity, enrollment='absent'))
        for result in ((1, {'reboot_required': True}), (0, {})):
            with self.subTest(result=result):
                self.action.return_value = result
                self.click('Certificate Enrollment')
                self.respond('continue')
                self.worker()
                self.tutorial.assert_not_called()

    def test_pending_identity_with_bad_esp_offers_recovery_instead_of_reboot_confirmation(self):
        self.page(signing_identity=replace(self.identity, enrollment='pending', defaults_ready=False),
                  grub=replace(self.boot, active_chain='untrusted', deployment='unknown'))
        self.click('Certificate Enrollment')
        dialog = self.adw.MessageDialog.new.return_value
        self.assertFalse(any(c.args[0] == 'reboot' for c in dialog.add_response.call_args_list))
        self.assertIn('before restarting', self.adw.MessageDialog.new.call_args.args[2])
        self.tutorial.assert_not_called()
        self.thread.assert_not_called()
        self.reboot.assert_not_called()

    def test_restart_rechecks_actual_esp_after_confirmation(self):
        self.page(signing_identity=replace(self.identity, enrollment='pending'))
        self.click('Certificate Enrollment')
        self.collect.return_value = replace(self.facts, secure_boot=replace(self.facts.secure_boot,
            grub=replace(self.boot, active_chain='untrusted')))
        self.respond('reboot')
        self.worker()
        self.reboot.assert_not_called()
        self.assertIn('before restarting', self.adw.MessageDialog.new.call_args.args[2])

    def test_pending_enrollment_dialog_does_not_claim_enrollment_is_unnecessary(self):
        self.page(signing_identity=replace(self.identity, enrollment='pending', defaults_ready=False),
                  grub=GrubState())
        self.click('AnduinOS Bootloader')
        self.assertNotIn('not needed', self.adw.MessageDialog.new.call_args.args[2])
        self.assertIn('enrolled first', self.adw.MessageDialog.new.call_args.args[2])

    def test_each_row_exposes_a_status_icon_with_explanation(self):
        self.page(signing_identity=replace(self.identity, purpose='module-only'), grub=GrubState())
        for row in self.rows.values():
            suffixes = [call.args[0] for call in row.add_suffix.call_args_list]
            self.assertEqual(len(suffixes), 1)
            suffixes[0].set_child.assert_called_once()
            self.assertTrue(suffixes[0].set_tooltip_text.call_args.args[0])

    def test_old_trust_does_not_claim_new_unenrolled_identity_is_established(self):
        self.page(signing_identity=replace(self.identity, enrollment='absent', defaults_ready=False),
                  grub=GrubState())
        subtitle = self.row('Certificate Enrollment').set_subtitle.call_args.args[0]
        self.assertIn('not enrolled', subtitle)
        self.assertNotIn('No repairs', self.labels[-1].set_label.call_args.args[0])

    def test_firmware_restart_requires_explicit_confirmation(self):
        self.page(SecureBootStatus.DISABLED, next_boot=self.evidence())
        self.click('Secure Boot')
        dialog = self.adw.MessageDialog.new.return_value
        self.assertIn(unittest.mock.call('action', 'translated:Open Firmware Settings'),
                      dialog.add_response.call_args_list)
        dialog.set_default_response.assert_called_with('cancel')
        for response in ['cancel', 'close', 'unexpected']:
            self.respond(response)
        self.thread.assert_not_called()
        self.firmware.assert_not_called()

    def test_certificate_change_requires_confirmation_and_cancellation_does_not_write(self):
        self.page(signing_identity=replace(self.identity, purpose='module-only'), grub=GrubState())
        self.click('Certificate Signing Purpose')
        self.respond('cancel')
        self.action.assert_not_called()
        self.thread.assert_not_called()

    def test_explicit_default_repair_uses_the_manual_helper_action(self):
        self.page(signing_identity=replace(self.identity, defaults_ready=False))
        self.click('Local MOK Certificate')
        self.respond('continue')
        self.worker()
        self.action.assert_called_once_with('repair-defaults')

    def test_installation_rechecks_before_running_the_fixed_action(self):
        self.page(grub=GrubState())
        self.click('AnduinOS Bootloader')
        self.respond('continue')
        self.worker()
        self.action.assert_called_once_with('install-bootloader')
        self.collect.assert_called()
        self.assertIn('Bootloader Updated', self.adw.MessageDialog.new.call_args.args[1])

    def test_certificate_change_after_confirmation_blocks_installation(self):
        self.page(grub=GrubState())
        self.click('AnduinOS Bootloader')
        self.collect.return_value = replace(self.facts, secure_boot=replace(self.facts.secure_boot,
            signing_identity=replace(self.identity, key_valid=False)))
        self.respond('continue')
        self.worker()
        self.action.assert_not_called()
        self.assertIn('Bootloader Update Failed', self.adw.MessageDialog.new.call_args.args[1])

    def test_successful_request_is_followed_by_inspection_not_optimistic_trust(self):
        self.page(signing_identity=replace(self.identity, enrollment='absent'))
        self.action.return_value = (0, {'reboot_required': True})
        self.click('Certificate Enrollment')
        self.respond('continue')
        self.worker()
        self.assertGreaterEqual(self.thread.call_count, 2)
        self.worker()  # Complete the post-operation inspection.
        self.assertIn('not enrolled', self.row('Certificate Enrollment').set_subtitle.call_args.args[0])

    def test_busy_operation_cannot_be_started_twice(self):
        self.page(grub=GrubState())
        self.click('AnduinOS Bootloader')
        self.respond('continue')
        self.click('AnduinOS Bootloader')
        self.thread.assert_called_once()

    def test_old_inspection_cannot_overwrite_a_newer_result(self):
        self.page()
        refresh = self.all_buttons[-1]
        refresh.connect.call_args.args[1](refresh)
        old_worker = self.thread.call_args.kwargs['target']
        refresh.connect.call_args.args[1](refresh)
        new_worker = self.thread.call_args.kwargs['target']
        self.idle.side_effect = None
        old_worker()
        old_draw = self.idle.call_args
        self.collect.return_value = replace(self.facts, secure_boot=replace(self.facts.secure_boot,
            signing_identity=replace(self.identity, enrollment='pending')))
        new_worker()
        new_draw = self.idle.call_args
        new_draw.args[0](*new_draw.args[1:])
        old_draw.args[0](*old_draw.args[1:])
        self.assertIn('Enrollment requested', self.row('Certificate Enrollment').set_subtitle.call_args.args[0])


class FirmwareSettingsTests(unittest.TestCase):
    def test_restart_uses_the_fixed_systemd_firmware_command(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        self.assertEqual(ui.restart_to_firmware_settings(runner), (True, ''))
        self.assertEqual(runner.call_args.args[0], ['systemctl', 'reboot', '--firmware-setup'])
        self.assertFalse(runner.call_args.kwargs['check'])
        self.assertEqual(runner.call_args.kwargs['timeout'], 10)

    def test_restart_returns_firmware_errors_to_the_ui(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 1, '', 'not supported'))
        self.assertEqual(ui.restart_to_firmware_settings(runner), (False, 'not supported'))


if __name__ == '__main__':
    unittest.main()
