"""Real trust-page widgets and dialogs; no host firmware or signing changes."""
import gettext
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib
from anduinos_secureboot import ui
from anduinos_secureboot.decision import Facts, NextBootEvidence
from anduinos_secureboot.identity import IdentityStatus
from anduinos_secureboot.model import DkmsState, GrubState, SecureBootState


def widgets(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widgets(child)
        child = child.get_next_sibling()


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
        return ui.create_secure_boot_page(initial_facts=Facts(state, DkmsState(),
            drivers_checked=True, next_boot=next_boot), translate=translate)

    def row(self, page, title):
        return next(w for w in widgets(page) if isinstance(w, Adw.ActionRow) and w.get_title() == title)

    def button(self, row):
        return next(w for w in widgets(row) if isinstance(w, Gtk.Button) and w.get_visible())

    def dialog(self, parent):
        return next(w for w in Gtk.Window.list_toplevels() if isinstance(w, Adw.MessageDialog)
                    and w.get_transient_for() is parent)

    def test_bootloader_row_reports_signature_trust_and_deployment_separately(self):
        cases = (
            (GrubState(), 'Install AnduinOS Bootloader', 'not installed', 'dim-label'),
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
                visible = [w for w in widgets(row) if isinstance(w, Gtk.Button) and w.get_visible()]
                self.assertEqual([w.get_label() for w in visible], [expected] if expected else [])
                action.assert_not_called()

    def test_bootloader_install_stays_clickable_but_explains_pending_enrollment(self):
        state = self.state(signing_identity=IdentityStatus(purpose='unified', enrollment='pending',
            key_present=True, key_valid=True, managed=True, defaults_ready=False), grub=GrubState())
        page = self.page(state)
        parent = Adw.Window(content=page)
        button = self.button(self.row(page, 'AnduinOS Bootloader'))
        self.assertTrue(button.get_sensitive())
        with patch.object(ui, 'run_action') as action:
            button.emit('clicked')
            dialog = self.dialog(parent)
            self.assertIn('enrolled first', dialog.get_body())
            self.assertNotIn('not needed', dialog.get_body())
            self.assertFalse(dialog.has_response('continue'))
            self.assertTrue(dialog.has_response('prerequisite'))
            action.assert_not_called()

    def test_pending_certificate_with_untrusted_esp_never_offers_restart_confirmation(self):
        from dataclasses import replace
        state = self.state(signing_identity=IdentityStatus(purpose='unified', enrollment='pending',
            key_present=True, key_valid=True, managed=True, defaults_ready=False))
        state = replace(state, grub=replace(state.grub, active_chain='untrusted', deployment='unknown'))
        page = self.page(state)
        parent = Adw.Window(content=page)
        with patch.object(ui, 'run_action') as action:
            self.button(self.row(page, 'Certificate Enrollment')).emit('clicked')
            dialog = self.dialog(parent)
            self.assertFalse(dialog.has_response('reboot'))
            self.assertIn('before restarting', dialog.get_body())
            self.assertEqual(self.button(self.row(page, 'AnduinOS Bootloader')).get_label(), 'Boot Recovery Help')
            action.assert_not_called()

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
                    self.button(self.row(page, 'AnduinOS Bootloader')).emit('clicked')
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
            self.button(self.row(page, 'AnduinOS Bootloader')).emit('clicked')
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
                self.assertEqual(self.button(self.row(page, title)).get_label(), label)
                action.assert_not_called()

    def test_disabled_secure_boot_still_explained_during_migration(self):
        page = self.page(self.state(enabled=False, signing_identity=IdentityStatus(
            purpose='module-only', enrollment='enrolled', key_present=True, key_valid=True)))
        subtitle = self.row(page, 'Secure Boot').get_subtitle()
        self.assertIn('disabled', subtitle)
        self.assertIn('still prepare', subtitle)

    def test_certificate_action_requires_confirmation_before_starting_work(self):
        page = self.page(self.state(signing_identity=IdentityStatus(
            purpose='module-only', enrollment='enrolled', key_present=True, key_valid=True)))
        parent = Adw.Window(content=page)
        with patch.object(ui, 'run_action') as action, patch.object(GLib, 'timeout_add') as delay:
            self.button(self.row(page, 'Certificate Signing Purpose')).emit('clicked')
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
                self.assertEqual(self.button(row).get_label(), translate('Restart to Enroll Certificate'))
                self.button(self.row(page, translate('AnduinOS Bootloader'))).emit('clicked')
                self.assertIn(translate(ui._MESSAGES['certificate-enrollment-required']), self.dialog(parent).get_body())
                parent.destroy()


if __name__ == '__main__':
    unittest.main()
