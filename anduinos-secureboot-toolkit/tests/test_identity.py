"""Real OpenSSL, simulated firmware; never enroll a key on the test host."""
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from anduinos_secureboot import identity
from anduinos_secureboot.inspect import inspect_dkms, enrolled_certificate_serials
from anduinos_secureboot.model import SecureBootState
from anduinos_secureboot.operations import CallableRunner, run_command


class Firmware:
    def __init__(self):
        self.enrolled = set()
        self.pending = set()
        self.calls = []
        self.import_error = False
        self.unknown = False

    def run(self, command, **kwargs):
        self.calls.append(tuple(command))
        if command[0] == 'openssl':
            return run_command(command, **kwargs)
        if command[:2] == ['mokutil', '--list-enrolled']:
            return subprocess.CompletedProcess(command, int(self.unknown), ''.join(f'SHA1 Fingerprint: {x}\n' for x in self.enrolled), '')
        if command[:2] == ['mokutil', '--list-new']:
            return subprocess.CompletedProcess(command, 0, ''.join(f'SHA1 Fingerprint: {x}\n' for x in self.pending), '')
        if command[:2] == ['mokutil', '--import']:
            if not self.import_error:
                self.pending = {hashlib.sha1(Path(command[2]).read_bytes()).hexdigest()}
            return subprocess.CompletedProcess(command, int(self.import_error), '', '')
        if command == ['mokutil', '--revoke-import']:
            self.pending.clear()
            return subprocess.CompletedProcess(command, 0, '', '')
        if command == ['mokutil', '--timeout', '-1']:
            return subprocess.CompletedProcess(command, 0, '', '')
        raise AssertionError(f'Unexpected command: {command}')


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = tempfile.TemporaryDirectory()
        cls.key = Path(cls.material.name) / 'MOK.priv'
        cls.cert = Path(cls.material.name) / 'MOK.der'
        identity.generate_identity(cls.key, cls.cert, run_command)

    @classmethod
    def tearDownClass(cls):
        cls.material.cleanup()

    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.firmware = Firmware()
        self.runner = CallableRunner(self.firmware.run)
        self.enterContext(patch('anduinos_secureboot.operations._firmware_supports_secure_boot', return_value=True))

    def candidate(self):
        name = 'a' * 32
        key, cert = identity.identity_paths(self.root, name)
        (self.root / 'identities').mkdir(mode=0o755)
        key.parent.mkdir(mode=0o755)
        key.write_bytes(self.key.read_bytes())
        key.chmod(0o600)
        cert.write_bytes(self.cert.read_bytes())
        identity.write_pointer(self.root, 'candidate', name)
        return key, cert

    def test_generated_certificate_has_both_purposes_and_private_permissions(self):
        text = run_command(['openssl', 'x509', '-inform', 'DER', '-in', str(self.cert), '-text', '-noout']).stdout
        self.assertIn('Code Signing', text)
        self.assertIn('Digital Signature', text)
        self.assertIn('CA:FALSE', text)
        self.assertNotIn(identity.MODULE_ONLY_OID, text)
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        identity.check_pair(self.key, self.cert, run_command)

    def test_partial_pair_is_never_overwritten(self):
        key, cert = self.candidate()
        before = cert.read_bytes()
        key.unlink()
        with self.assertRaises(ValueError):
            identity.generate_identity(key, cert, run_command)
        self.assertEqual(cert.read_bytes(), before)
        self.assertFalse(key.exists())

    def test_mismatch_and_broad_permissions_are_rejected(self):
        key, cert = self.candidate()
        key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'owner'):
            identity.check_pair(key, cert, run_command)
        key.chmod(0o600)
        key.write_text('not a private key')
        with self.assertRaisesRegex(ValueError, 'match'):
            identity.check_pair(key, cert, run_command)

    def test_prepare_retries_same_identity_and_never_runs_driver_or_boot_commands(self):
        key, cert = self.candidate()
        before = (key.read_bytes(), cert.read_bytes())
        for _ in range(2):
            result = identity.manage_identity('prepare-identity', self.firmware.run, self.root)
            self.assertTrue(result.ok)
            self.assertTrue(result.reboot_required)
        self.assertEqual(before, (key.read_bytes(), cert.read_bytes()))
        self.assertEqual(sum(c[:2] == ('mokutil', '--import') for c in self.firmware.calls), 1)
        self.assertTrue(all(c[0] in {'mokutil', 'openssl'} for c in self.firmware.calls))
        self.assertFalse((self.root / 'active.json').exists())

    def test_pending_is_not_trusted_or_selected(self):
        self.candidate()
        identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertEqual(identity.inspect_identity(self.runner, self.root).enrollment, 'pending')
        self.assertFalse(identity.inspect_identity(self.runner, self.root).usable)
        self.assertEqual(identity.selected_paths(self.root, self.runner), (identity.LEGACY_KEY, identity.LEGACY_CERTIFICATE))
        self.assertFalse((self.root / 'active.json').exists())

    def test_enrollment_immediately_selects_identity_without_writes_or_privileged_action(self):
        key, cert = self.candidate()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        old = 'b' * 40
        self.firmware.enrolled = {old, hashlib.sha1(cert.read_bytes()).hexdigest()}
        self.assertTrue(identity.inspect_identity(self.runner, self.root).usable)
        self.assertEqual(identity.selected_paths(self.root, self.runner), (key, cert))
        self.assertIn(old, self.firmware.enrolled)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertTrue(all(c[0] == 'openssl' or c[:2] in {('mokutil', '--list-enrolled'), ('mokutil', '--list-new')} for c in self.firmware.calls))

    def test_cleared_mok_reenrolls_active_identity_without_generating_another_key(self):
        _key, cert = self.candidate()
        fingerprint = hashlib.sha1(cert.read_bytes()).hexdigest()
        self.firmware.enrolled = {fingerprint}
        self.assertEqual(identity.selected_paths(self.root, self.runner)[1], cert)
        self.firmware.enrolled.clear()
        result = identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertTrue(result.reboot_required)
        self.assertEqual(identity.pointer(self.root, 'candidate'), 'a' * 32)
        self.assertEqual(self.firmware.pending, {fingerprint})

    def test_unknown_mok_and_failed_import_fail_closed(self):
        self.candidate()
        self.firmware.unknown = True
        with self.assertRaisesRegex(ValueError, 'verified'):
            identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.firmware.unknown = False
        self.firmware.import_error = True
        with self.assertRaisesRegex(ValueError, 'not confirmed'):
            identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertFalse((self.root / 'active.json').exists())

    def test_foreign_pending_requests_are_preserved(self):
        self.firmware.pending = {'b' * 40}
        with self.assertRaisesRegex(ValueError, 'already pending'):
            identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertEqual(self.firmware.pending, {'b' * 40})
        self.assertFalse((self.root / 'candidate.json').exists())

    def test_corrupt_metadata_cannot_redirect_privileged_paths(self):
        (self.root / 'candidate.json').write_text('{"identity": "../../another-key"}')
        with self.assertRaises(ValueError):
            identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertEqual(identity.inspect_identity(self.runner, self.root).purpose, 'unknown')

    def test_valid_legacy_identity_is_reenrolled_without_changing_its_key(self):
        with patch.object(identity, 'LEGACY_KEY', self.key), patch.object(identity, 'LEGACY_CERTIFICATE', self.cert):
            result = identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertTrue(result.reboot_required)
        key, cert = identity.identity_paths(self.root, identity.current_identity(self.root))
        self.assertEqual(key.read_bytes(), self.key.read_bytes())
        self.assertEqual(cert.read_bytes(), self.cert.read_bytes())

    def test_regeneration_keeps_old_identity_and_other_enrollments(self):
        key, cert = self.candidate()
        old = (key.read_bytes(), cert.read_bytes())
        identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.firmware.enrolled = {'b' * 40}
        result = identity.manage_identity('regenerate-identity', self.firmware.run, self.root)
        self.assertTrue(result.reboot_required)
        self.assertEqual((key.read_bytes(), cert.read_bytes()), old)
        self.assertEqual(self.firmware.enrolled, {'b' * 40})
        self.assertNotEqual(identity.current_identity(self.root), 'a' * 32)

    def test_regeneration_keeps_enrolled_identity_in_use_until_replacement_is_enrolled(self):
        key, cert = self.candidate()
        self.firmware.enrolled = {hashlib.sha1(cert.read_bytes()).hexdigest()}
        identity.manage_identity('regenerate-identity', self.firmware.run, self.root)
        self.assertEqual(identity.selected_paths(self.root, self.runner), (key, cert))
        self.firmware.enrolled.update(self.firmware.pending)
        self.firmware.pending.clear()
        self.assertEqual(identity.selected_paths(self.root, self.runner), identity.identity_paths(self.root, identity.current_identity(self.root)))

    def test_unreadable_enrollment_or_missing_key_never_automatically_selects_identity(self):
        key, cert = self.candidate()
        self.firmware.enrolled = {hashlib.sha1(cert.read_bytes()).hexdigest()}
        self.firmware.unknown = True
        self.assertEqual(identity.selected_paths(self.root, self.runner), (identity.LEGACY_KEY, identity.LEGACY_CERTIFICATE))
        self.firmware.unknown = False
        key.unlink()
        self.assertEqual(identity.selected_paths(self.root, self.runner), (identity.LEGACY_KEY, identity.LEGACY_CERTIFICATE))

    def test_manual_activation_action_no_longer_exists(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            identity.manage_identity('activate-identity', self.firmware.run, self.root)
        self.assertEqual(self.firmware.calls, [])

    def test_untrusted_store_is_rejected_before_any_key_or_enrollment_changes(self):
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'administrator'):
            identity.manage_identity('prepare-identity', self.firmware.run, self.root)
        self.assertEqual(self.firmware.calls, [])

    def test_unknown_firmware_blocks_all_identity_actions(self):
        self.candidate()
        with patch('anduinos_secureboot.operations._firmware_supports_secure_boot', return_value=False):
            for action in ('prepare-identity', 'regenerate-identity'):
                identity.manage_identity(action, self.firmware.run, self.root)
        self.assertEqual(self.firmware.calls, [])
        self.assertFalse((self.root / 'active.json').exists())

    def test_old_module_remains_trusted_after_selecting_new_identity(self):
        module = self.root / 'hid-xpadneo.ko.zst'
        module.write_bytes(b'fixture')
        state = SecureBootState(True, True, True, True, 'aa', enrolled_certificate_serials=('bb',))
        runner = CallableRunner(lambda command, **kwargs: subprocess.CompletedProcess(command, 0, 'BB', ''))
        dkms = inspect_dkms(state, runner, self.root)
        self.assertTrue(dkms.ready)
        self.assertFalse(dkms.details[0].signed_by_current_certificate)
        self.assertFalse(inspect_dkms(replace(state, enrolled_certificate_serials=()), runner, self.root).ready)

    def test_serial_parser_handles_decimal_and_multiline_not_pending(self):
        output = 'Serial Number: 17 (0x11)\nSerial Number:\n    aa:bb:01\n'
        runner = CallableRunner(lambda command, **kwargs: subprocess.CompletedProcess(command, 0, output, ''))
        self.assertEqual(enrolled_certificate_serials(runner), ('11', 'aabb01'))


if __name__ == '__main__':
    unittest.main()
