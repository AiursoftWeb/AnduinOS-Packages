"""Settings window states; all package changes are mocked, user data is temporary."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))


class PredictionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('CONTROL_PANEL_UI_TESTS') != '1':
            raise RuntimeError('Run the gui test profile')
        from anduinos_control_panel import app, prediction_settings
        cls.ui = prediction_settings
        app.Adw.init()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HOME':self.temp.name, 'XDG_CONFIG_HOME':self.temp.name+'/.config', 'XDG_STATE_HOME':self.temp.name+'/.local/state'})
        self.env.start()
        self.owner = self.ui.Adw.Window()
        self.owner._run_streaming_package_change = Mock()
        self.owner._rebuild_categories = Mock()
        self.windows=[]

    def tearDown(self):
        for window in self.windows:window.destroy()
        self.owner.destroy()
        self.env.stop();self.temp.cleanup()

    def window(self, installed):
        with patch.object(self.ui,'package_installed',return_value=installed):
            window=self.ui.PredictionSettingsWindow(self.owner)
        self.windows.append(window)
        return window

    def test_installed_opens_settings_without_install_action(self):
        window=self.window(True)
        self.assertFalse(window.install_row.get_visible())
        self.assertEqual({k:r.get_active() for k,r in window.switches.items()},self.ui.config.DEFAULTS)
        self.owner._run_streaming_package_change.assert_not_called()

    def test_not_installed_allows_configuration_then_install_and_retry(self):
        window=self.window(False)
        self.assertTrue(window.install_row.get_visible())
        window.switches['enabled'].set_active(False)
        self.assertFalse(self.ui.config.read_settings()['enabled'])
        with patch.object(self.ui,'package_installed',return_value=False):window._install(None)
        args=self.owner._run_streaming_package_change.call_args.args
        self.assertEqual(args[0],'anduinos-bash-guess-command')
        self.assertFalse(window.install_button.get_sensitive())
        args[-1]('cancelled')
        self.assertTrue(window.install_button.get_sensitive())
        with patch.object(self.ui,'package_installed',return_value=False):window._install(None)
        with patch.object(self.ui,'package_installed',return_value=True):
            self.owner._run_streaming_package_change.call_args.args[-2]()
        self.assertFalse(window.install_row.get_visible())
        self.assertFalse(window.switches['enabled'].get_active())

    def test_history_dependency_restore_and_clear_cancellation(self):
        window=self.window(True)
        window.switches['persist'].set_active(True)
        window.switches['history'].set_active(False)
        self.assertFalse(window.switches['persist'].get_sensitive())
        self.assertFalse(window.switches['persist'].get_active())
        state=self.ui.config.state_directory();state.mkdir(parents=True)
        (state/'history-v1').write_text('keep on restore')
        window._reset(None)
        self.assertEqual(self.ui.config.read_settings(),self.ui.config.DEFAULTS)
        self.assertTrue((state/'history-v1').exists())
        with patch.object(self.ui.Adw,'MessageDialog') as dialog_type, patch.object(window,'_clear') as clear:
            dialog=dialog_type.return_value
            window._confirm_clear(None)
            callback=dialog.connect.call_args.args[1]
            callback(dialog,'cancel');clear.assert_not_called()
            callback(dialog,'clear');clear.assert_called_once()

    def test_confirmed_clear_finishes_without_changing_settings(self):
        window = self.window(True)
        state = self.ui.config.state_directory()
        state.mkdir(parents=True)
        (state / 'history-v1').write_text('old learning')
        before = self.ui.config.read_settings()
        window._clear()
        self.assertFalse(window.clear_button.get_sensitive())
        deadline = time.monotonic() + 5
        context = self.ui.GLib.MainContext.default()
        while window.busy and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(.01)
        self.assertFalse(window.busy)
        self.assertTrue(window.clear_button.get_sensitive())
        self.assertFalse((state / 'history-v1').exists())
        self.assertEqual(self.ui.config.read_settings(), before)

    def test_unreadable_settings_stay_disabled_after_install(self):
        with patch.object(self.ui.config, 'read_settings', side_effect=OSError('unreadable')):
            window = self.window(False)
        with patch.object(self.ui, 'package_installed', return_value=False):
            window._install(None)
        self.owner._run_streaming_package_change.call_args.args[-1]('cancelled')
        self.assertFalse(window.switches['enabled'].get_sensitive())
        self.assertTrue(window.reset_button.get_sensitive())
