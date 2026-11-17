import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from anduinos_rescue_center.encryption import unlock_volume, lock_volume
from anduinos_rescue_center.model import Partition


class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.outer = Partition('/dev/vda3', '/dev/vda', 10000, 'crypto_luks', 'outer',
                               '', 'part', (), 'a' * 64, encrypted=True)
        self.inner = replace(self.outer, path='/dev/mapper/preopened', filesystem='ext4',
                             filesystem_uuid='inner', identity='b' * 64,
                             container_path=self.outer.path, container_identity=self.outer.identity,
                             luks_uuid='outer')
        self.calls = []

    def run_command(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '', '')

    def test_preopened_user_mapping_is_reused_and_never_owned_or_closed(self):
        with (patch('anduinos_rescue_center.encryption.resolve_target', return_value=self.outer),
              patch('anduinos_rescue_center.encryption._mapping', return_value=self.inner),
              patch('anduinos_rescue_center.encryption.inspect_target', return_value={'schema': 1})):
            payload = unlock_volume(self.outer.path, self.outer.identity, 'password', run=self.run_command)
        self.assertEqual(payload['unlock_session'], '')
        self.assertFalse(any('open' in c or 'close' in c for c, _ in self.calls))

    def test_wrong_password_goes_only_to_stdin_and_never_opens_a_filesystem(self):
        def run(command, **kwargs):
            self.calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, int('open' in command), '', 'wrong password')

        with tempfile.TemporaryDirectory() as directory:
            with (patch('anduinos_rescue_center.encryption.resolve_target', return_value=self.outer),
                  patch('anduinos_rescue_center.encryption._mapping', return_value=self.outer),
                  patch('anduinos_rescue_center.encryption.inspect_target') as inspect,
                  patch.object(Path, 'lstat', return_value=os.stat_result((0o40700, 0, 0, 1, 0, 0, 0, 0, 0, 0)))):
                with self.assertRaisesRegex(RuntimeError, 'Check the password'):
                    unlock_volume(self.outer.path, self.outer.identity, 'secret-test', run=run,
                                  runtime=Path(directory))
                inspect.assert_not_called()
        opens = [(c, k) for c, k in self.calls if 'open' in c]
        self.assertEqual(opens[0][1]['input'], 'secret-test')
        self.assertNotIn('secret-test', ' '.join(opens[0][0]))

    def test_running_encrypted_system_cannot_be_opened(self):
        with patch('anduinos_rescue_center.encryption.resolve_target',
                   return_value=replace(self.outer, active_system=True)):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                unlock_volume(self.outer.path, self.outer.identity, 'password', run=self.run_command)
        self.assertEqual(self.calls, [])

    def test_cleanup_requires_own_receipt_and_an_unmounted_mapping(self):
        session = '1' * 32
        path = '/dev/mapper/anduinos-rescue-' + session
        for mounted in (False, True):
            with self.subTest(mounted=mounted), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                receipt = runtime / (session + '.json')
                receipt.write_text(json.dumps({'path': path, 'identity': self.inner.identity, 'uid': os.getuid()}))
                part = replace(self.inner, path=path, mountpoints=('/somewhere',) if mounted else ())
                with (patch('anduinos_rescue_center.encryption.resolve_target', return_value=part),
                      patch.object(Path, 'lstat', return_value=os.stat_result((0o100600, 0, 0, 1, 0, 0, 0, 0, 0, 0)))):
                    if mounted:
                        with self.assertRaisesRegex(RuntimeError, 'Unmount'):
                            lock_volume(session, run=self.run_command, runtime=runtime)
                        self.assertTrue(receipt.exists())
                    else:
                        self.assertTrue(lock_volume(session, run=self.run_command, runtime=runtime)['closed'])
                        self.assertFalse(receipt.exists())
        self.assertEqual([c for c, _ in self.calls], [['cryptsetup', 'close', '--', path]])
