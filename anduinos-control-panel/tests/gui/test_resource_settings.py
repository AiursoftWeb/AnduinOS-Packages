"""Resource settings edit/apply behavior on an isolated GTK display."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))


class ResourceSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('CONTROL_PANEL_UI_TESTS') != '1':
            raise RuntimeError('Use the isolated gui test profile.')
        from anduinos_control_panel import resource_settings as module
        cls.module = module
        module.Adw.init()

    def setUp(self):
        module = self.module
        self.owner = module.Adw.Window()
        self.operations = []
        def stream(commands, _buffer, _output, success, failure):
            self.operations.append((commands, success, failure))
        self.owner._run_streaming_commands = stream
        self.owner._append_package_output = lambda buffer, _output, text: buffer.insert(buffer.get_end_iter(), text)
        self.state = {
            'values': {'enabled': True, 'memory': '250M', 'cpu_weight': 300, 'io_weight': 300},
            'personal': {'enabled': True, 'memory': '250M', 'cpu_weight': 300, 'io_weight': 300},
            'sources': {}, 'personal_sources': {}, 'system_active': True,
            'user_active': True, 'gui_exists': True,
        }
        self.patches = [
            patch.object(module, '_', side_effect=lambda text: text),
            patch.object(module.Path, 'is_file', return_value=True),
            patch.object(module.subprocess, 'run', side_effect=lambda *a, **k:
                         subprocess.CompletedProcess(a[0], 0, json.dumps(self.state), '')),
        ]
        for item in self.patches:
            item.start()
        self.window = module.ResourceSettingsWindow(self.owner)
        self.settle()

    def tearDown(self):
        self.window.destroy()
        self.owner.destroy()
        for item in reversed(self.patches):
            item.stop()

    def settle(self):
        end = time.monotonic() + 3
        while self.window.loading:
            while self.module.GLib.MainContext.default().iteration(False):
                pass
            if time.monotonic() > end:
                self.fail('Resource settings did not finish loading')
            time.sleep(.005)

    def test_read_does_not_authenticate_and_discard_does_not_apply(self):
        self.assertEqual(self.patches[-1].target.run.call_args.args[0],
                         [self.module.HELPER, 'status'])
        self.assertFalse(self.window.apply.get_sensitive())
        self.window.enabled.set_active(False)
        self.assertTrue(self.window.apply.get_sensitive())
        self.assertFalse(self.window.memory_row.get_sensitive())
        self.window.close_button.emit('clicked')
        self.assertEqual(self.operations, [])

    def test_both_tunings_reach_one_authorized_operation(self):
        self.window.memory.set_value(384)
        self.window.priority.set_selected(2)
        self.window.apply.emit('clicked')
        commands, _success, _failure = self.operations[0]
        self.assertEqual(commands[0][:3], ['/usr/bin/pkexec', self.module.HELPER, 'set'])
        self.assertEqual(json.loads(commands[0][3]), {'memory_mib': 384, 'priority': 500})
        self.assertFalse(self.window.enabled.get_sensitive())
        self.assertFalse(self.window.close_button.get_sensitive())
        self.assertTrue(self.window.advanced.get_expanded())

    def test_failed_apply_reads_back_restored_settings(self):
        self.window.enabled.set_active(False)
        self.window.apply.emit('clicked')
        self.operations[0][2]('Authentication was cancelled.')
        self.settle()
        self.assertTrue(self.window.enabled.get_active())
        self.assertIn('Authentication was cancelled.', self.window.status.get_label())
        self.assertTrue(self.window.close_button.get_sensitive())
        self.assertFalse(self.window.apply.get_sensitive())

    def test_switch_does_not_rewrite_custom_values(self):
        state = {**self.state, 'values': {'enabled': True, 'memory': '8192M',
                                        'cpu_weight': 711, 'io_weight': 912}}
        self.window._loaded(state, None)
        self.assertEqual(self.window.priority.get_selected(), 3)
        self.assertEqual(self.window.memory.get_value_as_int(), 8192)
        self.window.enabled.set_active(False)
        self.assertEqual(self.window._payload(), {'enabled': False})

    def test_reset_waits_for_apply(self):
        self.window.reset.emit('clicked')
        self.assertEqual(self.operations, [])
        self.window.apply.emit('clicked')
        self.assertEqual(self.operations[0][0],
                         [['/usr/bin/pkexec', self.module.HELPER, 'reset']])

    def test_failed_status_prevents_saving_guessed_defaults(self):
        self.window._load_failed('Invalid administrator configuration')
        self.assertFalse(self.window.enabled.get_sensitive())
        self.assertFalse(self.window.apply.get_sensitive())
        self.assertTrue(self.window.retry.get_visible())

    def test_manual_disable_is_not_reported_applied_while_services_run(self):
        state = {**self.state, 'values': {**self.state['values'], 'enabled': False}}
        self.window._loaded(state, None)
        self.assertFalse(self.window.enabled.get_active())
        self.assertIn('protection is enabled', self.window.status.get_label())
        self.assertTrue(self.window.apply.get_sensitive())
