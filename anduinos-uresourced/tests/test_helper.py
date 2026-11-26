"""Exercise the installed helper's read and write paths without changing a service."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gi.repository import GLib

loader = SourceFileLoader('resource_helper', str(Path(__file__).resolve().parents[1] / 'scripts/helper'))
helper = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(helper)

MACHINE = """[AnduinOS]
Enabled=true
[ActiveUser]
MemoryMin=8192M
[AppBoost]
ActiveCPUWeight=711
ActiveIOWeight=912
[Custom]
Note=keep
"""


class ResourceHelperTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.machine = root / 'system.conf'
        self.machine.write_text(MACHINE)
        self.personal = root / 'uresourced.conf'
        self.enterContext(patch.object(helper, 'CONFIG', self.machine))
        self.enterContext(patch.object(helper, 'active', return_value=True))
        self.enterContext(patch.object(GLib, 'get_user_config_dir', return_value=temp.name))

    def test_missing_personal_file_uses_machine_settings(self):
        state = helper.status()
        self.assertEqual(state['personal'], state['values'])
        self.assertEqual(state['sources']['applications'], str(self.machine))
        self.assertFalse(state['personal_errors'])

    def test_invalid_personal_integer_only_resets_its_own_weight(self):
        self.personal.write_text('[AppBoost]\nActiveCPUWeight=bogus\nActiveIOWeight=740\n')
        state = helper.status()
        self.assertEqual(state['values']['cpu_weight'], 711)
        self.assertEqual(state['personal']['cpu_weight'], 100)
        self.assertEqual(state['personal']['io_weight'], 740)
        self.assertEqual(state['sources']['applications'], str(self.personal))
        self.assertEqual(len(state['personal_errors']), 1)
        self.assertIn('ActiveCPUWeight', state['personal_errors'][0])
        self.assertTrue(state['can_reset'])

    def test_personal_file_that_fails_to_load_uses_the_machine_file(self):
        self.personal.write_text('invalid before any key-file group\n')
        state = helper.status()
        self.assertEqual(state['values'], state['personal'])
        self.assertEqual(state['sources']['applications'], str(self.machine))
        self.assertIn(str(self.personal), state['personal_errors'][0])

    def test_personal_file_uses_its_own_defaults_and_upstream_limits(self):
        self.personal.write_text('[AppBoost]\nActiveIOWeight=10001\n')
        state = helper.status()
        self.assertEqual((state['personal']['cpu_weight'], state['personal']['io_weight']), (100, 10000))
        self.assertFalse(state['personal_errors'])

    def test_switch_preserves_custom_memory_weights_and_unrelated_keys(self):
        owned = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)
        with patch.object(Path, 'lstat', return_value=owned):
            helper.save({'enabled': False})
        state = helper.status()
        self.assertEqual(state['values'], {'enabled': False, 'memory': '8192M', 'cpu_weight': 711, 'io_weight': 912})
        self.assertEqual(helper.read().get('Custom', 'Note'), 'keep')

    def test_out_of_range_edits_do_not_modify_the_machine_file(self):
        for value in (31, 4097):
            with self.subTest(value=value), self.assertRaises(ValueError):
                helper.save({'memory_mib': value})
        self.assertEqual(self.machine.read_text(), MACHINE)
