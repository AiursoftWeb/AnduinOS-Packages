import gettext
import pathlib
import string
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'src'))
from anduinos_appearance import profiles


class FakeSettings:
    def __init__(self, enabled=None, disabled=None):
        self.values = {
            'enabled-extensions': list(profiles.PANEL_EXTENSIONS if enabled is None else enabled),
            'disabled-extensions': list(disabled or []),
        }
        self.events = []
        self.fail_write = False

    def get_strv(self, key):
        return list(self.values[key])

    def set_strv(self, key, values):
        self.events.append((key, list(values)))
        if self.fail_write:
            return False
        self.values[key] = list(values)
        return True


class ProfileTests(unittest.TestCase):
    def test_new_profile_messages_are_translated_with_matching_placeholders(self):
        root = pathlib.Path(__file__).parents[1]
        messages = (
            'GNOME', 'Switching desktop layout… Please wait.', '✓ Applied — {style}',
            'Failed to enable {extension}.', 'Failed to disable {extension}.',
            'Failed to switch desktop layout. Check the extension settings and try again.',
            'Desktop layout could not be applied', 'Close',
            'An extension is in an error state. Log out and back in before trying again.',
        )
        formatter = string.Formatter()
        def fields(text):
            return {field for _, field, _, _ in formatter.parse(text) if field}
        for catalog in (root / 'locale').glob('*/LC_MESSAGES/anduinos-appearance.mo'):
            with catalog.open('rb') as source:
                translation = gettext.GNUTranslations(source)
            for message in messages:
                with self.subTest(locale=catalog.parents[1].name, message=message):
                    self.assertIn(message, translation._catalog)
                    self.assertEqual(fields(message), fields(translation.gettext(message)))

    def test_effective_defaults_and_disabled_overrides(self):
        settings = FakeSettings(disabled=[profiles.PANEL_EXTENSIONS[0]])
        self.assertEqual(profiles.enabled_panel_extensions(settings), set(profiles.PANEL_EXTENSIONS[1:]))
        with mock.patch.object(profiles, 'detect_current', return_value=('classic', 'left')):
            self.assertEqual(profiles.detect_profile(settings), ('classic', 'left'))
            settings.values['disabled-extensions'] = list(profiles.PANEL_EXTENSIONS)
            self.assertEqual(profiles.detect_profile(settings), ('gnome', 'left'))

    def test_fast_and_slow_paths(self):
        for style in ('classic', 'separated', 'eleven'):
            self.assertFalse(profiles.requires_extension_changes(style, profiles.PANEL_EXTENSIONS))
            self.assertTrue(profiles.requires_extension_changes(style, []))
            self.assertTrue(profiles.requires_extension_changes(style, profiles.PANEL_EXTENSIONS[:1]))
        self.assertFalse(profiles.requires_extension_changes('gnome', []))
        self.assertTrue(profiles.requires_extension_changes('gnome', profiles.PANEL_EXTENSIONS))

    def apply(self, style, settings, layout_result=True):
        from gi.repository import Gio
        def settled(enabled=None):
            settings.events.append(('wait', enabled))
            return {uuid: {'state': 1} for uuid in profiles.PANEL_EXTENSIONS}
        with (
            mock.patch.object(profiles, 'wait_for_extensions', side_effect=settled),
            mock.patch.object(profiles, 'apply_style_and_position',
                              side_effect=lambda *a, **kw: settings.events.append(('layout', a)) or layout_result) as layout,
            mock.patch.object(Gio.Settings, 'sync'),
        ):
            profiles.apply_profile(style, 'top', 900, settings=settings)
        return layout

    def test_gnome_uses_one_key_write_and_preserves_other_extensions(self):
        settings = FakeSettings(enabled=[*profiles.PANEL_EXTENSIONS, 'ding'], disabled=['weather'])
        layout = self.apply('gnome', settings)
        layout.assert_not_called()
        self.assertEqual(settings.events, [
            ('wait', None), ('disabled-extensions', ['weather', *profiles.PANEL_EXTENSIONS]), ('wait', False),
        ])
        self.assertEqual(settings.get_strv('enabled-extensions'), [*profiles.PANEL_EXTENSIONS, 'ding'])

    def test_return_configures_then_unmasks_in_one_write(self):
        settings = FakeSettings(disabled=['weather', *profiles.PANEL_EXTENSIONS])
        layout = self.apply('separated', settings)
        layout.assert_called_once_with('separated', 'top', screen_height=900)
        self.assertEqual(settings.events, [
            ('wait', None), ('layout', ('separated', 'top')),
            ('disabled-extensions', ['weather']), ('wait', True),
        ])

    def test_partial_profile_does_not_restart_enabled_extensions(self):
        settings = FakeSettings(disabled=['weather', profiles.PANEL_EXTENSIONS[2]])
        self.apply('classic', settings)
        self.assertEqual([e for e in settings.events if e[0].endswith('-extensions')],
                         [('disabled-extensions', ['weather'])])

    def test_old_cli_configuration_restores_missing_uuids_while_masked(self):
        settings = FakeSettings(enabled=['ding'], disabled=['weather'])
        self.apply('classic', settings)
        self.assertEqual(settings.events, [
            ('wait', None), ('layout', ('classic', 'top')),
            ('disabled-extensions', ['weather', *profiles.PANEL_EXTENSIONS]), ('wait', None),
            ('enabled-extensions', ['ding', *profiles.PANEL_EXTENSIONS]), ('wait', None),
            ('disabled-extensions', ['weather']), ('wait', True),
        ])

    def test_existing_mask_is_not_rewritten_during_legacy_repair(self):
        settings = FakeSettings(enabled=['ding'], disabled=['weather', *profiles.PANEL_EXTENSIONS])
        self.apply('classic', settings)
        writes = [e for e in settings.events if e[0].endswith('-extensions')]
        self.assertEqual(writes, [('enabled-extensions', ['ding', *profiles.PANEL_EXTENSIONS]),
                                  ('disabled-extensions', ['weather'])])

    def test_layout_failure_never_changes_extension_settings(self):
        settings = FakeSettings(disabled=list(profiles.PANEL_EXTENSIONS))
        with self.assertRaises(profiles.ProfileSwitchError):
            self.apply('classic', settings, layout_result=False)
        self.assertFalse(any(e[0].endswith('-extensions') for e in settings.events))

    def test_locked_settings_are_reported(self):
        settings = FakeSettings()
        settings.fail_write = True
        with self.assertRaises(profiles.ProfileSwitchError) as raised:
            self.apply('gnome', settings)
        self.assertEqual(raised.exception.action, 'settings')

    def test_unknown_profile_is_rejected_before_mutation(self):
        settings = FakeSettings()
        with self.assertRaises(ValueError):
            profiles.apply_profile('unknown', 'bottom', settings=settings)
        self.assertEqual(settings.events, [])

    def wait(self, snapshots, enabled, timeout=2):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        iterator = iter(snapshots)
        last = snapshots[-1]
        def states():
            return next(iterator, last)
        with (
            mock.patch.object(profiles, 'extension_states', side_effect=states) as get,
            mock.patch.object(profiles.time, 'monotonic', side_effect=lambda: clock[0]),
            mock.patch.object(profiles.time, 'sleep', side_effect=sleep),
        ):
            result = profiles.wait_for_extensions(enabled, timeout)
        return result, clock[0], get.call_count

    def snapshot(self, state):
        return {uuid: {'state': state} for uuid in profiles.PANEL_EXTENSIONS}

    def test_wait_does_not_accept_deactivating_state_or_other_extension_rebase(self):
        transitional = self.snapshot(2)
        transitional['other'] = {'state': 7}
        result, elapsed, calls = self.wait([
            self.snapshot(1), self.snapshot(7), transitional, self.snapshot(2),
        ], False)
        self.assertEqual(result, self.snapshot(2))
        self.assertGreaterEqual(elapsed, 0.45)
        self.assertGreater(calls, 4)

    def test_error_is_never_successful_disable_and_requires_session_recovery(self):
        for target in (True, False, None):
            with self.subTest(target=target), self.assertRaises(profiles.ProfileSwitchError) as raised:
                self.wait([self.snapshot(3)], target)
            self.assertTrue(raised.exception.restart_required)

    def test_outdated_and_transitional_states_are_not_disabled(self):
        with self.assertRaises(profiles.ProfileSwitchError) as raised:
            self.wait([self.snapshot(4)], False)
        self.assertFalse(raised.exception.restart_required)
        for state in (1, 7, 8):
            with self.subTest(state=state), self.assertRaises(profiles.ProfileSwitchError) as raised:
                self.wait([self.snapshot(state)], False, timeout=0.2)
            self.assertEqual(raised.exception.action, 'timeout')

    def test_active_and_initialized_states(self):
        self.wait([self.snapshot(1)], True)
        self.wait([self.snapshot(6)], False)
        self.wait([{}], False)

    def test_stability_window_restarts_after_transition(self):
        _, elapsed, _ = self.wait([self.snapshot(2)] * 4 + [self.snapshot(7), self.snapshot(2)], False)
        self.assertGreaterEqual(elapsed, 0.55)

    def test_existing_shell_error_aborts_before_any_setting_or_layout_change(self):
        settings = FakeSettings()
        error = profiles.ProfileSwitchError('state', profiles.PANEL_EXTENSIONS[1], restart_required=True)
        with (
            mock.patch.object(profiles, 'wait_for_extensions', side_effect=error),
            mock.patch.object(profiles, 'apply_style_and_position') as layout,
            self.assertRaises(profiles.ProfileSwitchError),
        ):
            profiles.apply_profile('gnome', 'bottom', settings=settings)
        layout.assert_not_called()
        self.assertEqual(settings.events, [])
