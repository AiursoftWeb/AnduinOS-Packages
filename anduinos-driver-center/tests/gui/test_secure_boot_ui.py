"""Real trust-page widgets and dialogs; no host firmware or signing changes."""
import gettext
from pathlib import Path
import unittest
import time
from unittest.mock import Mock, patch
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib
from anduinos_secureboot import ui
from anduinos_secureboot import enrollment
from anduinos_secureboot.decision import Facts, NextBootEvidence, decide
from anduinos_secureboot.identity import IdentityStatus
from anduinos_secureboot.model import DkmsState, GrubState, SecureBootState, SecureBootStatus


def widgets(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widgets(child)
        child = child.get_next_sibling()


def settle(seconds=0.15):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending() and time.monotonic() < deadline:
            context.iteration(False)
        time.sleep(0.005)


class SecureBootPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()

    def tearDown(self):
        for window in Gtk.Window.list_toplevels():
            if isinstance(window, Adw.MessageDialog):
                window.destroy()
        for window in Gtk.Window.list_toplevels():
            if isinstance(window, Adw.Window):
                window.destroy()

    def state(self, **changes):
        values = dict(enabled=True, key_present=True, certificate_present=True,
            enrolled=True, certificate_serial='old', setup_mode=False, boot_loader='shim',
            signing_identity=IdentityStatus(purpose='unified', enrollment='enrolled',
                key_present=True, key_valid=True, managed=True, defaults_ready=True),
            grub=GrubState(installed=True, signature='valid', enrollment='enrolled', deployment='ready', active_chain='trusted'))
        values.update(changes)
        return SecureBootState(**values)

    def page(self, state, translate=lambda value: value, next_boot=None):
        facts = Facts(state, DkmsState(), drivers_checked=True, next_boot=next_boot)
        page = ui.create_secure_boot_page(initial_facts=facts, translate=translate)
        decision = decide(facts)
        self.row_context = {row: (page, result, decision.next_row)
                            for row, result in zip((w for w in widgets(page)
                                if isinstance(w, Adw.ActionRow)), decision.rows)}
        return page

    def row(self, page, title):
        return next(w for w in widgets(page) if isinstance(w, Adw.ActionRow) and w.get_title() == title)

    def button(self, row):
        return next(w for w in widgets(row) if isinstance(w, Gtk.Button))

    def primary(self, page):
        return next(w for w in widgets(page) if isinstance(w, Gtk.Button)
                    and w.has_css_class('suggested-action'))

    def activate(self, row):
        page, result, next_row = self.row_context[row]
        if result.id == next_row and result.action and result.action.id != 'refresh':
            self.primary(page).emit('clicked')
        else:
            self.button(row).emit('clicked')
            if result.action and result.action.allowed:
                dialog = next(w for w in Gtk.Window.list_toplevels()
                              if isinstance(w, Adw.MessageDialog) and w.get_heading() == row.get_title())
                dialog.response('action')

    def dialog(self, parent):
        return next(w for w in Gtk.Window.list_toplevels() if isinstance(w, Adw.MessageDialog)
                    and w.get_transient_for() is parent)

    def test_missing_certificate_has_one_primary_action_and_no_duplicate_row_buttons(self):
        identity = IdentityStatus(purpose='missing', enrollment='absent', key_present=False,
                                  key_valid=False)
        page = self.page(self.state(signing_identity=identity, grub=None))
        buttons = [w for w in widgets(page) if isinstance(w, Gtk.Button) and w.get_visible()]
        self.assertEqual(sum(w.get_label() == 'Create Signing Certificate' for w in buttons), 1)
        self.assertEqual(sum(w.has_css_class('suggested-action') for w in buttons), 1)
        self.assertTrue(all(w.get_label() is None
                            for row in widgets(page) if isinstance(row, Adw.ActionRow)
                            for w in widgets(row) if isinstance(w, Gtk.Button)))
        parent = Adw.Window(content=page)
        with patch.object(ui, 'run_action') as action:
            self.activate(self.row(page, 'Local MOK Certificate'))
            self.assertEqual(self.dialog(parent).get_default_response(), 'cancel')
            action.assert_not_called()

    def test_main_page_never_offers_two_recommended_operations(self):
        from dataclasses import replace
        healthy = self.state()
        cases = (
            (healthy, 0),
            (replace(healthy, enabled=False, status=SecureBootStatus.DISABLED), 0),
            (replace(healthy, enabled=False, status=SecureBootStatus.UNKNOWN), 0),
            (replace(healthy, enabled=False, status=SecureBootStatus.UNSUPPORTED), 0),
            (replace(healthy, grub=GrubState()), 1),
            (replace(healthy, signing_identity=IdentityStatus(purpose='missing', enrollment='absent'),
                     grub=GrubState()), 1),
            (replace(healthy, signing_identity=replace(healthy.signing_identity, purpose='module-only'),
                     grub=GrubState()), 1),
            (replace(healthy, signing_identity=replace(healthy.signing_identity, enrollment='pending'),
                     grub=GrubState()), 1),
            (replace(healthy, signing_identity=replace(healthy.signing_identity, key_valid=False),
                     grub=replace(healthy.grub, active_chain='untrusted')), 1),
        )
        for state, expected in cases:
            with self.subTest(state=state):
                page = self.page(state)
                recommendations = [w for w in widgets(page) if isinstance(w, Gtk.Button)
                                   and w.get_visible() and w.has_css_class('suggested-action')]
                self.assertEqual(len(recommendations), expected)
                # Inspection, failure, and independent fixes never become a second row action.
                self.assertTrue(all(w.get_label() is None
                                    for row in widgets(page) if isinstance(row, Adw.ActionRow)
                                    for w in widgets(row) if isinstance(w, Gtk.Button)))

    def test_warning_icon_explains_prerequisites_before_offering_a_repair(self):
        state = self.state(signing_identity=IdentityStatus(purpose='module-only', enrollment='enrolled',
            key_present=True, key_valid=True, defaults_ready=True), grub=GrubState())
        page = self.page(state)
        parent = Adw.Window(content=page)
        icon = self.button(self.row(page, 'AnduinOS Bootloader'))
        self.assertIn('both the bootloader and drivers', icon.get_tooltip_text())
        with patch.object(ui, 'run_action') as action, patch.object(GLib, 'timeout_add') as delay:
            icon.emit('clicked')
            dialog = self.dialog(parent)
            self.assertIn('both the bootloader and drivers', dialog.get_body())
            self.assertEqual(dialog.get_response_label('action'), 'Create Signing Certificate')
            self.assertEqual(dialog.get_default_response(), 'close')
            action.assert_not_called()
            delay.assert_not_called()
            dialog.response('action')
            confirmation = self.dialog(parent)
            self.assertEqual(confirmation.get_default_response(), 'cancel')
            self.assertTrue(confirmation.has_response('continue'))
            action.assert_not_called()

    def test_prepared_disabled_system_opens_translated_firmware_confirmation(self):
        locale_dir = Path(__file__).resolve().parents[2] / 'locale'
        for language in ('en_GB', 'zh_CN'):
            with self.subTest(language=language):
                translate = gettext.translation('anduinos-driver-center', localedir=locale_dir,
                                                languages=[language]).gettext
                state = self.state(enabled=False)
                with patch.object(ui, 'restart_to_firmware_settings') as restart:
                    page = self.page(state, translate)
                    parent = Adw.Window(content=page)
                    button = self.button(self.row(page, translate('Secure Boot')))
                    self.assertIsNone(button.get_label())
                    prepared = translate(ui._MESSAGES['secure-boot-prepared'])
                    if language == 'zh_CN':
                        self.assertNotEqual(prepared, ui._MESSAGES['secure-boot-prepared'])
                    self.assertTrue(any(isinstance(w, Gtk.Label) and w.get_label() == prepared
                                        for w in widgets(page)))
                    button.emit('clicked')
                    details = self.dialog(parent)
                    self.assertEqual(details.get_response_label('action'), translate('Open Firmware Settings'))
                    details.response('action')
                    dialog = self.dialog(parent)
                    self.assertTrue(dialog.has_response('reboot'))
                    self.assertEqual(dialog.get_default_response(), 'cancel')
                    self.assertEqual(dialog.get_body(), translate(ui._FIRMWARE_INSTRUCTIONS))
                    dialog.response('cancel')
                    restart.assert_not_called()
                    parent.destroy()

    def test_firmware_button_reports_real_unsigned_bootloader_without_restart(self):
        from dataclasses import replace
        state = self.state(enabled=False)
        state = replace(state, grub=replace(state.grub, signature='missing'))
        with patch.object(ui, 'restart_to_firmware_settings') as restart:
            page = self.page(state)
            parent = Adw.Window(content=page)
            self.button(self.row(page, 'Secure Boot')).emit('clicked')
            dialog = self.dialog(parent)
            self.assertIn('needs signing', dialog.get_body())
            self.assertFalse(dialog.has_response('reboot'))
            self.assertEqual(dialog.get_response_label('action'), 'Repair AnduinOS Bootloader')
            restart.assert_not_called()

    def test_bootloader_row_reports_signature_trust_and_deployment_separately(self):
        cases = (
            (GrubState(), 'Install AnduinOS Bootloader', 'recommended', 'warning'),
            (GrubState(installed=True, active_chain="trusted"), 'Repair AnduinOS Bootloader', 'needs signing', 'warning'),
            (GrubState(installed=True, signature='valid', enrollment='pending', active_chain='trusted'), 'Repair AnduinOS Bootloader', 'not enrolled', 'warning'),
            (GrubState(installed=True, signature='valid', enrollment='enrolled', deployment='pending', active_chain='trusted'), 'Repair AnduinOS Bootloader', 'needs deployment', 'warning'),
            (GrubState(installed=True, signature='valid', enrollment='enrolled', deployment='ready', active_chain='trusted'), None, 'signed, trusted and deployed', 'success'),
        )
        for boot, expected, text, css in cases:
            with self.subTest(boot=boot), patch.object(ui, 'run_action') as action:
                page = self.page(self.state(grub=boot))
                row = self.row(page, 'AnduinOS Bootloader')
                self.assertTrue(row.get_visible())
                self.assertIn(text, row.get_subtitle())
                self.assertTrue(any(isinstance(w, Gtk.Image) and w.has_css_class(css) for w in widgets(row)))
                self.assertEqual(self.primary(page).get_visible(), expected is not None)
                if expected:
                    self.assertEqual(self.primary(page).get_label(), expected)
                action.assert_not_called()

    def test_bootloader_install_stays_clickable_but_explains_pending_enrollment(self):
        state = self.state(signing_identity=IdentityStatus(purpose='unified', enrollment='pending',
            key_present=True, key_valid=True, managed=True, defaults_ready=False), grub=GrubState())
        page = self.page(state)
        parent = Adw.Window(content=page)
        button = self.button(self.row(page, 'AnduinOS Bootloader'))
        self.assertTrue(button.get_sensitive())
        with patch.object(ui, 'run_action') as action:
            self.activate(self.row(page, 'AnduinOS Bootloader'))
            dialog = self.dialog(parent)
            self.assertIn('enrolled first', dialog.get_body())
            self.assertNotIn('not needed', dialog.get_body())
            self.assertFalse(dialog.has_response('continue'))
            self.assertTrue(dialog.has_response('action'))
            action.assert_not_called()

    def test_pending_certificate_with_untrusted_esp_never_offers_restart_confirmation(self):
        from dataclasses import replace
        state = self.state(signing_identity=IdentityStatus(purpose='unified', enrollment='pending',
            key_present=True, key_valid=True, managed=True, defaults_ready=False))
        state = replace(state, grub=replace(state.grub, active_chain='untrusted', deployment='unknown'))
        page = self.page(state)
        parent = Adw.Window(content=page)
        with patch.object(ui, 'run_action') as action:
            self.activate(self.row(page, 'Certificate Enrollment'))
            dialog = self.dialog(parent)
            self.assertFalse(dialog.has_response('reboot'))
            self.assertIn('before restarting', dialog.get_body())
            self.assertEqual(self.primary(page).get_label(), 'Boot Recovery Help')
            action.assert_not_called()

    def test_pending_enrollment_tutorial_plays_in_a_small_restart_dialog(self):
        identity = IdentityStatus(purpose='unified', enrollment='pending',
            key_present=True, key_valid=True, managed=True, defaults_ready=False)
        page = self.page(self.state(signing_identity=identity))
        parent = Adw.Window(content=page, default_width=640, default_height=480)
        parent.present()
        with patch.object(ui, 'run_action') as action:
            self.activate(self.row(page, 'Certificate Enrollment'))
            dialog = self.dialog(parent)
            settle()
            tutorial = dialog.get_extra_child()
            self.assertIsInstance(tutorial, enrollment.EnrollmentTutorial)
            self.assertTrue(tutorial.get_mapped())
            self.assertEqual(dialog.get_default_response(), 'cancel')
            self.assertLessEqual(dialog.get_width(), parent.get_width())
            self.assertLessEqual(dialog.get_height(), parent.get_height())
            for name in ('cancel', 'reboot'):
                button = dialog.get_response_label(name)
                self.assertTrue(any(isinstance(w, Gtk.Button) and w.get_label() == button
                                    and w.get_mapped() for w in widgets(dialog)))
            first = tutorial.get_child().get_paintable().save_to_png_bytes().get_data()
            settle(0.25)
            later = tutorial.get_child().get_paintable().save_to_png_bytes().get_data()
            self.assertNotEqual(first, later)  # A real animation, not its first still.
            tutorial.set_visible(False)
            self.assertEqual(tutorial._timeout, 0)
            self.assertIsNone(tutorial._animation)
            tutorial.set_visible(True)
            self.assertIsNotNone(tutorial._animation)
            next(w for w in widgets(dialog) if isinstance(w, Gtk.Button)
                 and w.get_label() == dialog.get_response_label('cancel')).emit('clicked')
            settle(0.3)
            self.assertEqual(tutorial._timeout, 0)
            action.assert_not_called()

    def test_missing_tutorial_keeps_enrollment_instructions_and_restart_controls(self):
        identity = IdentityStatus(purpose='unified', enrollment='pending',
            key_present=True, key_valid=True, managed=True, defaults_ready=False)
        page = self.page(self.state(signing_identity=identity))
        parent = Adw.Window(content=page)
        parent.present()
        with (patch.object(enrollment.GdkPixbuf.PixbufAnimation, 'new_from_file',
                           side_effect=GLib.Error('Missing tutorial')),
              self.assertLogs(enrollment.__name__, level='WARNING')):
            self.activate(self.row(page, 'Certificate Enrollment'))
            settle()
        dialog = self.dialog(parent)
        self.assertIn('123456', dialog.get_body())
        self.assertTrue(dialog.has_response('reboot'))
        self.assertFalse(dialog.get_extra_child().get_visible())

    def test_queued_enrollment_result_keeps_ok_visible_with_long_instructions(self):
        identity = IdentityStatus(purpose='unified', enrollment='absent',
            key_present=True, key_valid=True, managed=True, defaults_ready=False)
        state = self.state(signing_identity=identity)
        page = self.page(state)
        parent = Adw.Window(content=page, default_width=640, default_height=480)
        parent.present()
        settle()
        with (patch.object(ui, 'run_action', return_value=(0, {'reboot_required': True})),
              patch.object(ui, 'collect_facts', return_value=Facts(state, DkmsState(), drivers_checked=True))):
            self.activate(self.row(page, 'Certificate Enrollment'))
            confirmation = self.dialog(parent)
            next(w for w in widgets(confirmation) if isinstance(w, Gtk.Button)
                 and w.get_label() == confirmation.get_response_label('continue')).emit('clicked')
            settle(0.8)
        dialog = next(w for w in Gtk.Window.list_toplevels()
                      if isinstance(w, Adw.MessageDialog) and w.get_heading() == 'Operation Finished')
        self.assertTrue(dialog.get_extra_child().get_mapped())
        self.assertIn('123456', dialog.get_body())
        self.assertLessEqual(dialog.get_width(), parent.get_width())
        self.assertLessEqual(dialog.get_height(), parent.get_height())
        button = next(w for w in widgets(dialog) if isinstance(w, Gtk.Button)
                      and w.get_label() == dialog.get_response_label('ok'))
        ok, bounds = button.compute_bounds(dialog)
        self.assertTrue(ok)
        self.assertLessEqual(bounds.get_y() + bounds.get_height(), dialog.get_height())

    def test_invalid_or_unchecked_private_key_never_shows_healthy_or_allows_signing(self):
        for valid in (False, None):
            with self.subTest(valid=valid):
                identity = IdentityStatus(purpose='unified', enrollment='enrolled', key_present=True,
                    key_valid=valid, managed=True, detail='Pair verification failed', defaults_ready=True)
                page = self.page(self.state(signing_identity=identity, grub=GrubState(installed=True, active_chain="trusted")))
                certificate = self.row(page, 'Local MOK Certificate')
                self.assertEqual(certificate.get_tooltip_text(), identity.detail)
                self.assertFalse(any(isinstance(w, Gtk.Image) and w.has_css_class('success')
                                     for w in widgets(certificate)))
                parent = Adw.Window(content=page)
                with patch.object(ui, 'run_action') as action:
                    self.activate(self.row(page, 'AnduinOS Bootloader'))
                    self.assertIn('signing key', self.dialog(parent).get_body())
                    action.assert_not_called()
                parent.destroy()

    def test_bootloader_result_keeps_logs_in_bounded_details(self):
        log = 'debconf: diagnostic <not markup>\n' * 1000
        for code in (0, 1):
            dialog = ui._bootloader_result_dialog(None, code,
                {'error': 'failure reason' if code else '', 'steps': {'packages': {'detail': log}}}, lambda t: t)
            try:
                self.assertEqual(dialog.get_heading(), 'Bootloader Updated' if code == 0 else 'Bootloader Update Failed')
                self.assertNotIn('debconf', dialog.get_body())
                expander = dialog.get_extra_child()
                self.assertIsInstance(expander, Gtk.Expander)
                self.assertEqual(expander.get_expanded(), code != 0)
                scroll = expander.get_child()
                self.assertEqual(scroll.get_property('height-request'), 180)
                view = scroll.get_child()
                buffer = view.get_buffer()
                self.assertIn(log, buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True))
                self.assertFalse(view.get_editable())
            finally:
                dialog.destroy()

    def test_bootloader_result_without_logs_explains_post_operation_check(self):
        dialog = ui._bootloader_result_dialog(None, 0, {}, lambda t: t)
        try:
            self.assertIsNone(dialog.get_extra_child())
            self.assertIn('checked again', dialog.get_body())
        finally:
            dialog.destroy()

    def test_bootloader_action_shows_progress_before_starting_privileged_work(self):
        page = self.page(self.state(grub=GrubState()))
        parent = Adw.Window(content=page)
        with patch.object(ui, 'run_action') as action, patch.object(GLib, 'timeout_add') as delay:
            self.activate(self.row(page, 'AnduinOS Bootloader'))
            self.dialog(parent).response('continue')
            self.assertTrue(any(isinstance(w, Adw.MessageDialog) and w.get_heading() == 'Working…'
                                for w in Gtk.Window.list_toplevels()))
            self.assertEqual(delay.call_args.args[0], 200)
            action.assert_not_called()

    def test_certificate_migration_actions_match_actual_enrollment(self):
        cases = (
            ('module-only', 'enrolled', None, 'Certificate Signing Purpose', 'Create Signing Certificate'),
            ('missing', 'absent', None, 'Local MOK Certificate', 'Create Signing Certificate'),
            ('unified', 'pending', False, 'Certificate Enrollment', 'Restart to Enroll Certificate'),
            ('unified', 'absent', False, 'Certificate Enrollment', 'Submit Enrollment Request'),
            ('unified', 'enrolled', False, 'Local MOK Certificate', 'Repair Automatic Signing'),
        )
        for purpose, enrollment, defaults, title, label in cases:
            with self.subTest(purpose=purpose, enrollment=enrollment), patch.object(ui, 'run_action') as action:
                identity = IdentityStatus(purpose=purpose, enrollment=enrollment, key_present=purpose != 'missing',
                    key_valid=True, managed=True, defaults_ready=defaults)
                page = self.page(self.state(signing_identity=identity))
                self.assertEqual(self.primary(page).get_label(), label)
                action.assert_not_called()

    def test_disabled_secure_boot_still_explained_during_migration(self):
        page = self.page(self.state(enabled=False, signing_identity=IdentityStatus(
            purpose='module-only', enrollment='pending', key_present=True, key_valid=True)))
        subtitle = self.row(page, 'Secure Boot').get_subtitle()
        self.assertIn('disabled', subtitle)
        self.assertIn('still prepare', subtitle)

    def test_certificate_action_requires_confirmation_before_starting_work(self):
        page = self.page(self.state(signing_identity=IdentityStatus(
            purpose='module-only', enrollment='enrolled', key_present=True, key_valid=True)))
        parent = Adw.Window(content=page)
        with patch.object(ui, 'run_action') as action, patch.object(GLib, 'timeout_add') as delay:
            self.activate(self.row(page, 'Certificate Signing Purpose'))
            dialog = self.dialog(parent)
            self.assertEqual(dialog.get_default_response(), 'cancel')
            action.assert_not_called()
            delay.assert_not_called()
            dialog.response('continue')
            self.assertEqual(delay.call_args.args[0], 200)
            action.assert_not_called()

    def test_english_and_chinese_rows_have_translated_actions_and_blocker_explanations(self):
        locale_dir = Path(__file__).resolve().parents[2] / 'locale'
        for language in ('en_GB', 'zh_CN'):
            with self.subTest(language=language):
                translate = gettext.translation('anduinos-driver-center', localedir=locale_dir,
                                                languages=[language]).gettext
                if language == 'zh_CN':
                    self.assertNotEqual(translate('Certificate Enrollment'), 'Certificate Enrollment')
                state = self.state(enabled=False, signing_identity=IdentityStatus(
                    purpose='unified', enrollment='pending', key_present=True, key_valid=True), grub=GrubState())
                page = self.page(state, translate)
                parent = Adw.Window(content=page)
                row = self.row(page, translate('Certificate Enrollment'))
                self.assertIn(translate(ui._MESSAGES['certificate-pending']), row.get_subtitle())
                self.assertEqual(self.primary(page).get_label(), translate('Restart to Enroll Certificate'))
                self.activate(self.row(page, translate('AnduinOS Bootloader')))
                self.assertIn(translate(ui._MESSAGES['certificate-enrollment-required']), self.dialog(parent).get_body())
                parent.destroy()


if __name__ == '__main__':
    unittest.main()
