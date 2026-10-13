"""Signing default migration against temporary files and simulated MOKs."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anduinos_secureboot import defaults, identity
from anduinos_secureboot.operations import CONFIG_CONTENT, run_command
from test_identity import Firmware


class DefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = tempfile.TemporaryDirectory()
        cls.key = Path(cls.material.name) / 'key'
        cls.cert = Path(cls.material.name) / 'cert'
        identity.generate_identity(cls.key, cls.cert, run_command)

    @classmethod
    def tearDownClass(cls):
        cls.material.cleanup()

    def setUp(self):
        self.enterContext(patch('anduinos_secureboot.operations._firmware_supports_secure_boot', return_value=True))
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.base / 'identities-store'
        self.key, self.cert = identity.identity_paths(self.root, 'a' * 32)
        self.key.parent.mkdir(parents=True)
        self.key.write_bytes(type(self).key.read_bytes())
        self.key.chmod(0o600)
        self.cert.write_bytes(type(self).cert.read_bytes())
        identity.write_pointer(self.root, 'current', 'a' * 32)
        self.legacy = self.base / 'mok'
        self.legacy.mkdir()
        (self.legacy / 'MOK.priv').write_bytes(b'old private key')
        (self.legacy / 'MOK.priv').chmod(0o600)
        (self.legacy / 'MOK.der').write_bytes(b'old certificate')
        self.config = self.base / 'dkms/framework.conf.d/anduinos-sb-sign.conf'
        self.config.parent.mkdir(parents=True)
        self.config.write_text(CONFIG_CONTENT)
        for path in self.base.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o600 if path.name == 'MOK.priv' else 0o644)
        self.firmware = Firmware()
        self.fingerprint = hashlib.sha1(self.cert.read_bytes()).hexdigest()
        self.firmware.enrolled = {'b' * 40, self.fingerprint}

    def sync(self):
        return defaults.sync_defaults(self.firmware.run, self.root, self.legacy, self.config)

    def ready(self):
        return defaults.defaults_ready(self.key, self.cert, self.legacy, self.config)

    def before(self):
        return [(p, p.read_bytes()) for p in (self.config, self.legacy / 'MOK.priv', self.legacy / 'MOK.der')]

    def test_enrolled_pair_becomes_default_with_private_backups_and_no_module_commands(self):
        before = self.before()
        self.assertFalse(self.ready())
        self.assertTrue(self.sync().ok)
        self.assertTrue(self.ready())
        self.assertEqual((self.legacy / 'MOK.priv').read_bytes(), self.key.read_bytes())
        identity.check_pair(self.legacy / 'MOK.priv', self.legacy / 'MOK.der', run_command)
        backup, = (self.root / 'backups').iterdir()
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        for i, (path, data) in enumerate(before):
            saved = backup / f'{i}-{path.name}'
            self.assertEqual(saved.read_bytes(), data)
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.firmware.enrolled, {'b' * 40, self.fingerprint})
        self.assertTrue(all(c[0] == 'openssl' or c[:2] == ('mokutil', '--list-enrolled') for c in self.firmware.calls))

    def test_repeat_is_idempotent_and_does_not_replace_backup(self):
        self.sync()
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob('*')}
        self.assertEqual(self.sync().steps['defaults'].status, 'skipped')
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.root.rglob('*')})

    def test_pending_or_absent_enrollment_never_switches_defaults(self):
        before = self.before()
        self.firmware.enrolled = {'b' * 40}
        for pending in (set(), {self.fingerprint}):
            self.firmware.pending = pending
            self.assertEqual(self.sync().steps['defaults'].status, 'skipped')
            self.assertEqual(before, self.before())
        self.assertFalse((self.root / 'backups').exists())

    def test_unknown_enrollment_fails_closed(self):
        before = self.before()
        self.firmware.unknown = True
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(before, self.before())

    def test_unknown_firmware_blocks_all_writes(self):
        before = self.before()
        with patch('anduinos_secureboot.operations._firmware_supports_secure_boot', return_value=False):
            self.sync()
        self.assertEqual(before, self.before())
        self.assertEqual(self.firmware.calls, [])

    def test_enrolled_module_only_certificate_is_rejected(self):
        original = identity.inspect_identity
        from dataclasses import replace
        with patch.object(identity, 'inspect_identity', side_effect=lambda runner, root: replace(
                original(runner, root), purpose='module-only')):
            with self.assertRaisesRegex(ValueError, 'unified'):
                self.sync()
        self.assertFalse(self.ready())

    def test_enrolled_but_missing_key_is_rejected(self):
        self.key.unlink()
        with self.assertRaisesRegex(ValueError, 'unified'):
            self.sync()

    def test_new_override_is_visible_even_after_successful_sync(self):
        self.sync()
        self.assertTrue(self.ready())
        (self.config.parent / 'z-custom.conf').write_text('mok_certificate="/other/cert"\n')
        self.assertFalse(self.ready())

    def test_invalid_key_never_overwrites_working_files(self):
        before = self.before()
        self.key.write_text('invalid')
        with self.assertRaisesRegex(ValueError, 'match'):
            self.sync()
        self.assertEqual(before, self.before())

    def test_redirected_destination_is_rejected(self):
        cert = self.legacy / 'MOK.der'
        cert.unlink()
        cert.symlink_to(self.cert)
        with self.assertRaisesRegex(ValueError, 'Redirected'):
            self.sync()

    def test_insecure_key_permissions_are_rejected(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'owner'):
            self.sync()

    def test_custom_config_and_competing_overrides_require_review(self):
        self.config.write_text('sign_file=/custom\n')
        with self.assertRaisesRegex(ValueError, 'requires review'):
            self.sync()
        self.config.write_text(CONFIG_CONTENT)
        override = self.config.parent / 'z-custom.conf'
        override.write_text('mok_signing_key="/another/key"\n')
        with self.assertRaisesRegex(ValueError, 'requires review'):
            self.sync()
        self.assertFalse(self.ready())

    def test_failed_write_rolls_back_and_retry_succeeds(self):
        before = self.before()
        original = defaults.atomic_write
        failed = False

        def write(path, data, mode):
            nonlocal failed
            if path == self.legacy / 'MOK.der' and not failed:
                failed = True
                raise OSError('simulated full disk')
            return original(path, data, mode)

        with patch.object(defaults, 'atomic_write', side_effect=write):
            with self.assertRaisesRegex(OSError, 'full disk'):
                self.sync()
        self.assertEqual(before, self.before())
        self.sync()
        self.assertTrue(self.ready())
        self.assertEqual(len(list((self.root / 'backups').iterdir())), 2)

    def test_interrupted_copy_is_completed_without_overwriting_original_backup(self):
        self.sync()
        original_backups = {p: p.read_bytes() for p in (self.root / 'backups').rglob('*') if p.is_file()}
        (self.legacy / 'MOK.der').write_text('interrupted old certificate')
        self.assertFalse(self.ready())
        self.sync()
        self.assertTrue(self.ready())
        self.assertTrue(all(p.read_bytes() == data for p, data in original_backups.items()))

    def test_no_identity_never_creates_one(self):
        (self.root / 'current.json').unlink()
        before = self.before()
        self.assertEqual(self.sync().steps['defaults'].status, 'skipped')
        self.assertEqual(self.firmware.calls, [])
        self.assertEqual(before, self.before())

    def test_manage_already_enrolled_identity_converges_without_import_or_manual_use(self):
        original = defaults.sync_defaults
        with patch('anduinos_secureboot.operations._firmware_supports_secure_boot', return_value=True), patch.object(
                defaults, 'sync_defaults', side_effect=lambda run, root: original(run, root, self.legacy, self.config)):
            result = identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertTrue(result.ok)
        self.assertFalse(result.reboot_required)
        self.assertTrue(self.ready())
        self.assertFalse(any(c[:2] == ('mokutil', '--import') for c in self.firmware.calls))

    def test_package_wires_boot_and_upgrade_convergence(self):
        package = Path(__file__).resolve().parents[1]
        service = (package / 'data/anduinos-signing-defaults.service').read_text()
        self.assertIn('Before=display-manager.service dkms.service apt-daily.service', service)
        self.assertIn('ExecStart=/usr/libexec/anduinos-secureboot-helper sync-defaults', service)
        self.assertIn('sync-defaults', (package / 'scripts/postinst.sh').read_text())
        self.assertIn('anduinos-signing-defaults.service" AutoEnable="true"',
                      (package / 'anduinos-secureboot-toolkit.aosproj').read_text())
