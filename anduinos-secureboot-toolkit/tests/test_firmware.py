"""Exercise the customer's exit 255 against independent EFI evidence."""
import errno
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from anduinos_secureboot.firmware import EFI_GLOBAL, probe_firmware, FirmwareEvidence, recover_firmware
from anduinos_secureboot.model import SecureBootStatus as Status

class FirmwareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.variables = self.root / 'efivars'
        self.variables.mkdir()
        self.mountinfo = self.root / 'mountinfo'
        self.mountinfo.write_text(f'30 20 0:30 / {self.variables} rw - efivarfs efivarfs rw\n')

    def probe(self):
        return probe_firmware(efi_path=self.root, mountinfo=self.mountinfo,
            run=lambda *a, **kw: subprocess.CompletedProcess(
                [], 255, '', "This system doesn't support Secure Boot\n"))

    def flag(self, name, value):
        (self.variables / f'{name}-{EFI_GLOBAL}').write_bytes(b'\x06\0\0\0' + bytes([value]))

    def test_customer_output_with_confirmed_absence(self):
        evidence = self.probe()
        self.assertEqual(evidence.status, Status.UNSUPPORTED)
        self.assertTrue(evidence.uefi)
        self.assertEqual(evidence.returncode, 255)

    def test_unmounted_is_unknown(self):
        self.mountinfo.write_text('')
        evidence = self.probe()
        self.assertFalse(evidence.known)
        self.assertEqual(evidence.reason, 'efivarfs-unmounted')

    def test_permission_and_io_errors(self):
        for error, reason in ((PermissionError(errno.EACCES, 'denied'), 'permission-denied'),
                              (OSError(errno.EIO, 'failed'), 'efi-read-failed')):
            with self.subTest(reason=reason), patch('anduinos_secureboot.firmware._read_flag', side_effect=error):
                evidence = self.probe()
                self.assertFalse(evidence.known)
                self.assertEqual(evidence.reason, reason)

    def test_valid_flags_recover_failed_mokutil(self):
        for secure, setup, expected in ((1, 0, Status.ENABLED), (0, 0, Status.DISABLED), (0, 1, Status.DISABLED)):
            with self.subTest(secure=secure, setup=setup):
                self.flag('SecureBoot', secure)
                self.flag('SetupMode', setup)
                self.assertEqual(self.probe().status, expected)

    def test_missing_and_invalid_setup(self):
        self.flag('SecureBoot', 1)
        self.assertFalse(self.probe().known)
        for value in (b'', b'\0' * 4, b'\0' * 4 + b'\x02', b'\0' * 6):
            (self.variables / f'SetupMode-{EFI_GLOBAL}').write_bytes(value)
            self.assertFalse(self.probe().known)

    def test_registered_variable_read_enoent_is_unknown(self):
        self.flag('SecureBoot', 1)
        with patch('anduinos_secureboot.firmware._read_flag', side_effect=FileNotFoundError):
            self.assertEqual(self.probe().reason, 'efi-read-failed')

    @patch('anduinos_secureboot.firmware.os.geteuid', return_value=1000)
    def test_helper_recovers_permissions(self, _uid):
        after = FirmwareEvidence(True, Status.ENABLED, 'efi-variables')
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, after.to_json(), '')
        self.assertEqual(recover_firmware(FirmwareEvidence(True, Status.UNKNOWN, 'permission-denied'), run=run), after)
        self.assertEqual(calls, [['pkexec', '/usr/libexec/anduinos-firmware-probe']])

    @patch('anduinos_secureboot.firmware.os.geteuid', return_value=1000)
    def test_cancelled_helper_stays_unknown(self, _uid):
        evidence = recover_firmware(FirmwareEvidence(True, Status.UNKNOWN, 'permission-denied'),
            run=lambda *a, **kw: subprocess.CompletedProcess([], 126, '', 'cancelled'))
        self.assertFalse(evidence.known)
        self.assertEqual(evidence.reason, 'helper-failed')

    @patch('anduinos_secureboot.firmware.os.geteuid', return_value=0)
    def test_missing_interface_prepared_only_in_live(self, _uid):
        from anduinos_secureboot.firmware import prepare_live_interface
        self.mountinfo.write_text('')
        live_image = self.root / 'rootfs.squashfs'
        calls = []
        def prepare():
            prepare_live_interface(efi_path=self.root, mountinfo=self.mountinfo,
                live_image=live_image, run=lambda command, **kwargs: calls.append(command))
        prepare()
        self.assertEqual(calls, [])
        live_image.touch()
        prepare()
        self.assertEqual(calls, [['/usr/bin/mount', '-t', 'efivarfs', '-o',
            'nosuid,nodev,noexec', 'efivarfs', str(self.variables)]])
        self.mountinfo.write_text(f'30 20 0:30 / {self.variables} rw - efivarfs efivarfs rw\n')
        prepare()
        self.assertEqual(len(calls), 1)

    @patch('anduinos_secureboot.firmware.os.geteuid', return_value=1000)
    def test_unknown_io_does_not_request_key_operations_or_repeat_privilege(self, _uid):
        evidence = FirmwareEvidence(True, Status.UNKNOWN, 'efi-read-failed')
        def unexpected(*args, **kwargs):
            self.fail('I/O failure must not start a helper operation')
        self.assertEqual(recover_firmware(evidence, run=unexpected), evidence)

    def test_contradictory_flags_remain_unknown(self):
        self.flag('SecureBoot', 1)
        self.flag('SetupMode', 1)
        self.assertFalse(self.probe().known)
