import hashlib
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.model import (
    Architecture,
    BootSpec,
    Firmware,
    MokPasswordPolicy,
    SecureBoot,
)
from installer_core.secure_boot import (
    MOK_CERTIFICATE,
    MOK_ENROLLMENT_PASSWORD,
    MOK_MARKER,
    MOK_PRIVATE_KEY,
    EnrollSecureBootStep,
    PrepareSecureBootStep,
    VerifyDkmsSignaturesStep,
    _verify_signed_efi_chain,
)
from installer_core.steps import InstallContext


class KeyGeneratingRunner(FakeRunner):
    """Real OpenSSL only; all firmware/chroot commands remain simulated."""
    def __init__(self, target: Path):
        super().__init__()
        self.target = target

    def run(self, command, **kwargs):
        if command[0] == "openssl":
            self.commands.append((tuple(command), kwargs))
            for flag in ('-in', '-keyout', '-out'):
                if flag in command:
                    assert Path(command[command.index(flag) + 1]).resolve().is_relative_to(self.target.resolve())
            return subprocess.run(command, capture_output=True, text=True,
                                  timeout=kwargs.get('timeout', 30), check=kwargs.get('check', False))
        result = super().run(command, **kwargs)
        if command[-2:] == ('/usr/libexec/anduinos-grub-update', 'first-install') and result.returncode == 0:
            # Model the signing output without executing chroot or touching EFI.
            for suffix, platform in (('x64', 'x86_64'), ('aa64', 'arm64')):
                if (self.target / f'usr/lib/anduinos-grub/grub{suffix}.efi').is_file():
                    signed = self.target / f'usr/lib/grub/{platform}-efi-signed/grub{suffix}.efi.signed'
                    signed.parent.mkdir(parents=True, exist_ok=True)
                    signed.touch()
        return result


def prepare_secure_boot_target(
    target: Path, architecture: Architecture = Architecture.AMD64
) -> None:
    signed = (
        (
            "usr/lib/shim/shimx64.efi.signed.latest",
            "usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed",
        )
        if architecture is Architecture.AMD64
        else (
            "usr/lib/shim/shimaa64.efi.signed.latest",
            "usr/lib/grub/arm64-efi-signed/grubaa64.efi.signed",
        )
    )
    for relative in (
        "usr/bin/mokutil",
        "usr/bin/openssl",
        *signed,
    ):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def prepare_signed_efi_chain(target: Path, suffix: str = "x64") -> None:
    for relative in (
        f"boot/efi/EFI/AnduinOS/shim{suffix}.efi",
        f"boot/efi/EFI/AnduinOS/grub{suffix}.efi",
    ):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


class PrepareSecureBootTests(unittest.TestCase):
    architecture = Architecture.AMD64
    suffix = 'x64'
    platform = 'x86_64'

    def test_own_grub_is_signed_after_creating_target_certificate_without_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            source = target / f'usr/lib/anduinos-grub/grub{self.suffix}.efi'
            source.parent.mkdir(parents=True)
            source.touch()
            (target / f'usr/lib/grub/{self.platform}-efi-signed/grub{self.suffix}.efi.signed').unlink()
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _message: None, {'target': target})
            PrepareSecureBootStep(runner).execute(context)
            commands = [call[0] for call in runner.commands]
            signing = commands.index(('chroot', str(target), '/usr/libexec/anduinos-grub-update', 'first-install'))
            generation = next(index for index, command in enumerate(commands) if command[:2] == ('openssl', 'req'))
            self.assertGreater(signing, generation)
            self.assertTrue((target / MOK_PRIVATE_KEY).is_file())
            self.assertFalse(any('--import' in command for command in commands))

    def test_own_grub_signing_failure_preserves_target_identity_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            source = target / f'usr/lib/anduinos-grub/grub{self.suffix}.efi'
            source.parent.mkdir(parents=True)
            source.touch()
            (target / f'usr/lib/grub/{self.platform}-efi-signed/grub{self.suffix}.efi.signed').unlink()
            runner = KeyGeneratingRunner(target)
            command = ('chroot', str(target), '/usr/libexec/anduinos-grub-update', 'first-install')
            runner.outputs[command] = ('', 'Injected signing failure', 1)
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _message: None, {'target': target})
            with self.assertRaisesRegex(RuntimeError, 'GRUB signing failed'):
                PrepareSecureBootStep(runner).execute(context)
            first = (target / MOK_CERTIFICATE).read_bytes()
            runner.outputs[command] = ('', '', 0)
            PrepareSecureBootStep(runner).execute(context)
            self.assertEqual(first, (target / MOK_CERTIFICATE).read_bytes())

    def test_replaces_unmarked_live_key_and_configures_dkms(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            private = target / MOK_PRIVATE_KEY
            certificate = target / MOK_CERTIFICATE
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_bytes(b"live-private")
            certificate.write_bytes(b"live-certificate")
            runner = KeyGeneratingRunner(target)
            context = InstallContext(
                valid_plan(architecture=self.architecture), lambda _message: None, {"target": target}
            )
            step = PrepareSecureBootStep(runner)
            step.execute(context)
            step.verify(context)

            self.assertNotEqual(private.read_bytes(), b"live-private")
            self.assertNotEqual(certificate.read_bytes(), b"live-certificate")
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
            marker = (target / MOK_MARKER).read_text().strip()
            self.assertEqual(
                marker, hashlib.sha256(certificate.read_bytes()).hexdigest()
            )
            dkms = (
                target
                / "etc/dkms/framework.conf.d/anduinos-sb-sign.conf"
            ).read_text()
            self.assertIn("MOK.priv", dkms)
            self.assertIn("MOK.der", dkms)

        generate, = [command for command, _ in runner.commands if command[:2] == ('openssl', 'req')]
        self.assertIn('rsa:3072', generate)
        self.assertIn('extendedKeyUsage=codeSigning', generate)
        self.assertFalse(any('update-secureboot-policy' in command for command, _ in runner.commands))

    def test_secure_boot_disabled_prepares_unified_identity_without_enrollment(self):
        base = valid_plan(architecture=self.architecture)
        plan = replace(
            base,
            platform=replace(base.platform, secure_boot=SecureBoot.DISABLED),
            boot=BootSpec(
                mok_password_policy=MokPasswordPolicy.NOT_APPLICABLE
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            # Disabled Secure Boot must not demand signed bootloader payloads.
            (target / f'usr/lib/grub/{self.platform}-efi-signed/grub{self.suffix}.efi.signed').unlink()
            runner = KeyGeneratingRunner(target)
            context = InstallContext(plan, lambda _message: None, {'target': target})
            step = PrepareSecureBootStep(runner)
            step.execute(context)
            step.verify(context)
            EnrollSecureBootStep(runner).execute(context)
            self.assertTrue(context.values['secure_boot_prepared'])
            self.assertFalse(context.values['mok_enrollment_pending'])
            self.assertTrue(all(command[0] == 'openssl' for command, _ in runner.commands))

    def test_unsupported_secure_boot_does_nothing(self):
        base = valid_plan(architecture=self.architecture)
        plan = replace(
            base,
            platform=replace(
                base.platform, secure_boot=SecureBoot.UNSUPPORTED
            ),
            boot=BootSpec(
                mok_password_policy=MokPasswordPolicy.NOT_APPLICABLE
            ),
        )
        runner = FakeRunner()
        context = InstallContext(plan, lambda _message: None)
        PrepareSecureBootStep(runner).execute(context)
        self.assertFalse(context.values["secure_boot_prepared"])
        self.assertEqual(runner.commands, [])

    def test_own_stack_prepares_portable_signed_image_without_secure_boot_support(self):
        base = valid_plan(architecture=self.architecture)
        plan = replace(base, platform=replace(base.platform, secure_boot=SecureBoot.UNSUPPORTED),
                       boot=BootSpec(mok_password_policy=MokPasswordPolicy.NOT_APPLICABLE))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            source = target / f'usr/lib/anduinos-grub/grub{self.suffix}.efi'
            source.parent.mkdir(parents=True)
            source.touch()
            (target / f'usr/lib/grub/{self.platform}-efi-signed/grub{self.suffix}.efi.signed').unlink()
            runner = KeyGeneratingRunner(target)
            context = InstallContext(plan, lambda _: None, {'target': target})
            step = PrepareSecureBootStep(runner)
            step.execute(context)
            step.verify(context)
            EnrollSecureBootStep(runner).execute(context)
            self.assertTrue((target / MOK_CERTIFICATE).is_file())
            self.assertIn(('chroot', str(target), '/usr/libexec/anduinos-grub-update', 'first-install'),
                          [command for command, _ in runner.commands])
            self.assertFalse(context.values['mok_enrollment_pending'])

    def test_arm64_requires_and_accepts_arm64_signed_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, Architecture.ARM64)
            runner = KeyGeneratingRunner(target)
            context = InstallContext(
                valid_plan(architecture=Architecture.ARM64),
                lambda _message: None,
                {"target": target},
            )
            step = PrepareSecureBootStep(runner)
            step.execute(context)
            step.verify(context)
        self.assertTrue(context.values["secure_boot_prepared"])

    def test_missing_signed_payload_fails_before_key_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            (
                target
                / f'usr/lib/grub/{self.platform}-efi-signed/grub{self.suffix}.efi.signed'
            ).unlink()
            runner = KeyGeneratingRunner(target)
            context = InstallContext(
                valid_plan(architecture=self.architecture), lambda _message: None, {"target": target}
            )
            with self.assertRaisesRegex(RuntimeError, "Signed.*missing"):
                PrepareSecureBootStep(runner).execute(context)
        self.assertFalse(
            any(
                command[:2] == ("openssl", "req")
                for command, _kwargs in runner.commands
            )
        )

    def test_retry_preserves_the_same_certificate_and_checks_its_purpose(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None, {'target': target})
            step = PrepareSecureBootStep(runner)
            step.execute(context)
            before = ((target / MOK_PRIVATE_KEY).read_bytes(), (target / MOK_CERTIFICATE).read_bytes())
            step.execute(context)
            step.verify(context)
            self.assertEqual(before, ((target / MOK_PRIVATE_KEY).read_bytes(), (target / MOK_CERTIFICATE).read_bytes()))
            self.assertEqual(sum(cmd[:2] == ('openssl', 'req') for cmd, _ in runner.commands), 1)

    def test_independent_installations_generate_different_unified_keys(self):
        fingerprints = []
        private_fingerprints = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                prepare_secure_boot_target(target, self.architecture)
                runner = KeyGeneratingRunner(target)
                step = PrepareSecureBootStep(runner)
                context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None, {'target': target})
                step.execute(context)
                step.verify(context)
                fingerprints.append(hashlib.sha256((target / MOK_CERTIFICATE).read_bytes()).hexdigest())
                private_fingerprints.append(hashlib.sha256((target / MOK_PRIVATE_KEY).read_bytes()).hexdigest())
                text = runner.run(['openssl', 'x509', '-inform', 'DER', '-in', str(target / MOK_CERTIFICATE), '-noout', '-text']).stdout
                self.assertIn('Code Signing', text)
                self.assertIn('Digital Signature', text)
                self.assertNotIn('1.3.6.1.4.1.2312.16.1.2', text)
        self.assertNotEqual(*fingerprints)
        self.assertNotEqual(*private_fingerprints)

    def test_driver_center_inspector_recognizes_fresh_identity_without_migration_metadata(self):
        from anduinos_secureboot import identity
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None, {'target': target})
            PrepareSecureBootStep(runner).execute(context)
            cert, key = target / MOK_CERTIFICATE, target / MOK_PRIVATE_KEY
            root = target / 'var/lib/anduinos-secureboot'
            with patch.object(identity, 'LEGACY_CERTIFICATE', cert), patch.object(identity, 'LEGACY_KEY', key):
                state = identity.inspect_identity(runner, root)
                self.assertEqual(state.purpose, 'unified')
                self.assertFalse(state.usable)
                runner.outputs[('mokutil', '--list-enrolled')] = (
                    'SHA1 Fingerprint: ' + hashlib.sha1(cert.read_bytes()).hexdigest() + '\n', '', 0)
                state = identity.inspect_identity(runner, root)
                self.assertTrue(state.usable)
                self.assertFalse(state.managed)
            self.assertFalse(root.exists())

    def test_marked_module_only_identity_is_rejected_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            key, cert = target / MOK_PRIVATE_KEY, target / MOK_CERTIFICATE
            key.parent.mkdir(parents=True)
            runner = KeyGeneratingRunner(target)
            runner.run(['openssl', 'req', '-new', '-x509', '-nodes', '-newkey', 'rsa:2048', '-days', '1',
                        '-subj', '/CN=Old module only', '-addext', 'extendedKeyUsage=codeSigning,1.3.6.1.4.1.2312.16.1.2',
                        '-outform', 'DER', '-keyout', str(key), '-out', str(cert)])
            marker = target / MOK_MARKER
            marker.parent.mkdir(parents=True)
            marker.write_text(hashlib.sha256(cert.read_bytes()).hexdigest())
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None, {'target': target})
            with self.assertRaisesRegex(ValueError, 'both GRUB'):
                PrepareSecureBootStep(runner).execute(context)
            self.assertNotIn('secure_boot_prepared', context.values)

    def test_missing_half_of_pair_is_regenerated_for_fresh_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None, {'target': target})
            step = PrepareSecureBootStep(runner)
            step.execute(context)
            old = (target / MOK_CERTIFICATE).read_bytes()
            (target / MOK_PRIVATE_KEY).unlink()
            step.execute(context)
            step.verify(context)
            self.assertNotEqual(old, (target / MOK_CERTIFICATE).read_bytes())

    def test_redirected_paths_or_copied_identity_store_fail_before_generation(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None, {'target': target})
            parent = target / MOK_PRIVATE_KEY.parent
            parent.parent.mkdir(parents=True)
            parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'Redirected'):
                PrepareSecureBootStep(runner).execute(context)
            parent.unlink()
            (target / 'var/lib/anduinos-secureboot').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'identity store'):
                PrepareSecureBootStep(runner).execute(context)
            self.assertEqual(runner.commands, [])

    def test_missing_native_template_never_falls_back_to_ubuntu(self):
        for wrong_template in (False, True):
            for state in (SecureBoot.ENABLED, SecureBoot.DISABLED, SecureBoot.UNSUPPORTED):
                with self.subTest(wrong_template=wrong_template, state=state), tempfile.TemporaryDirectory() as directory:
                    target = Path(directory)
                    prepare_secure_boot_target(target, self.architecture)
                    source = target / 'usr/lib/anduinos-grub'
                    source.mkdir(parents=True)
                    if wrong_template:
                        other = 'aa64' if self.suffix == 'x64' else 'x64'
                        (source / f'grub{other}.efi').touch()
                    runner = KeyGeneratingRunner(target)
                    context = InstallContext(valid_plan(architecture=self.architecture, secure_boot=state),
                                             lambda _: None, {'target': target})
                    with self.assertRaisesRegex(RuntimeError, 'native GRUB template'):
                        PrepareSecureBootStep(runner).execute(context)
                    self.assertEqual(runner.commands, [])

    def test_preflight_requires_signing_tools_without_secure_boot_support(self):
        runner = FakeRunner()
        context = InstallContext(valid_plan(architecture=self.architecture, secure_boot=SecureBoot.UNSUPPORTED),
                                 lambda _: None)
        PrepareSecureBootStep(runner).preflight(context)
        self.assertIn('openssl', runner.required)
        self.assertIn('chroot', runner.required)

    def test_own_stack_signs_when_secure_boot_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            source = target / f'usr/lib/anduinos-grub/grub{self.suffix}.efi'
            source.parent.mkdir(parents=True)
            source.touch()
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(architecture=self.architecture, secure_boot=SecureBoot.DISABLED),
                                     lambda _: None, {'target': target})
            PrepareSecureBootStep(runner).execute(context)
            EnrollSecureBootStep(runner).execute(context)
            self.assertIn(('chroot', str(target), '/usr/libexec/anduinos-grub-update', 'first-install'),
                          [command for command, _ in runner.commands])
            self.assertFalse(context.values['mok_enrollment_pending'])


class BiosSecureBootTests(unittest.TestCase):
    def test_own_stack_prepares_portable_efi_without_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target)
            source = target / 'usr/lib/anduinos-grub/grubx64.efi'
            source.parent.mkdir(parents=True)
            source.touch()
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(firmware=Firmware.BIOS, secure_boot=SecureBoot.NOT_APPLICABLE),
                                     lambda _: None, {'target': target})
            step = PrepareSecureBootStep(runner)
            step.preflight(context)
            step.execute(context)
            step.verify(context)
            EnrollSecureBootStep(runner).execute(context)
            self.assertIn(('chroot', str(target), '/usr/libexec/anduinos-grub-update', 'first-install'),
                          [command for command, _ in runner.commands])
            self.assertFalse(context.values['mok_enrollment_pending'])


class Arm64PrepareSecureBootTests(PrepareSecureBootTests):
    architecture = Architecture.ARM64
    suffix = 'aa64'
    platform = 'arm64'


class EnrollSecureBootTests(unittest.TestCase):
    architecture = Architecture.AMD64
    suffix = 'x64'

    def test_foreign_pending_or_unknown_state_never_imports_a_key(self):
        cases = [
            ('--list-new', ('SHA1 Fingerprint: ' + 'b' * 40 + '\n', '', 0)),
            ('--list-new', ('', 'unreadable', 1)),
            ('--list-new', ('malformed', '', 0)),
            ('--list-enrolled', ('', 'unreadable', 1)),
        ]
        for operation, output in cases:
            with self.subTest(operation=operation, output=output), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                cert = target / MOK_CERTIFICATE
                cert.parent.mkdir(parents=True)
                cert.write_bytes(b'certificate')
                prepare_signed_efi_chain(target, self.suffix)
                runner = FakeRunner()
                runner.outputs[('chroot', str(target), 'mokutil', operation)] = output
                context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None,
                                         {'target': target, 'secure_boot_prepared': True})
                with self.assertRaises(RuntimeError):
                    EnrollSecureBootStep(runner).execute(context)
                self.assertFalse(any('--import' in cmd or '--revoke-import' in cmd for cmd, _ in runner.commands))

    def test_cancelled_request_can_be_retried_with_the_same_generated_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, self.architecture)
            prepare_signed_efi_chain(target, self.suffix)
            runner = KeyGeneratingRunner(target)
            context = InstallContext(valid_plan(architecture=self.architecture), lambda _: None, {'target': target})
            prepare = PrepareSecureBootStep(runner)
            prepare.execute(context)
            before = (target / MOK_CERTIFICATE).read_bytes()
            enrollment = EnrollSecureBootStep(runner)
            enrollment.execute(context)
            fingerprint = hashlib.sha1(before).hexdigest()
            pending_command = ('chroot', str(target), 'mokutil', '--list-new')
            runner.outputs[pending_command] = ('SHA1 Fingerprint: ' + fingerprint + '\n', '', 0)
            enrollment.verify(context)
            enrollment.execute(context)
            self.assertEqual(sum('--import' in cmd for cmd, _ in runner.commands), 1)
            # User cancels MOKManager. Retry neither rotates the key nor
            # requires any "Use" action after physical enrollment succeeds.
            runner.outputs[pending_command] = ('', '', 0)
            prepare.execute(context)
            enrollment.execute(context)
            self.assertEqual(sum('--import' in cmd for cmd, _ in runner.commands), 2)
            runner.outputs[('chroot', str(target), 'mokutil', '--list-enrolled')] = ('SHA1 Fingerprint: ' + fingerprint + '\n', '', 0)
            enrollment.execute(context)
            enrollment.verify(context)
            self.assertFalse(context.values['mok_enrollment_pending'])
            self.assertEqual(before, (target / MOK_CERTIFICATE).read_bytes())

    def test_password_is_only_sent_on_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            certificate = target / MOK_CERTIFICATE
            certificate.parent.mkdir(parents=True)
            certificate.write_bytes(b"certificate")
            prepare_signed_efi_chain(target, self.suffix)
            runner = FakeRunner()
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--list-new",
                )
            ] = ("", "", 0)
            context = InstallContext(
                valid_plan(architecture=self.architecture),
                lambda _message: None,
                {
                    "target": target,
                    "secure_boot_prepared": True,
                },
            )
            EnrollSecureBootStep(runner).execute(context)

        import_call = next(
            item for item in runner.commands if "--import" in item[0]
        )
        self.assertNotIn(MOK_ENROLLMENT_PASSWORD, repr(import_call[0]))
        self.assertEqual(
            import_call[1]["input_text"],
            f"{MOK_ENROLLMENT_PASSWORD}\n{MOK_ENROLLMENT_PASSWORD}\n",
        )
        self.assertIn(
            ("chroot", str(target), "mokutil", "--timeout", "-1"),
            [item[0] for item in runner.commands],
        )

    def test_vendor_only_chain_does_not_require_removable_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_signed_efi_chain(target, self.suffix)
            runner = FakeRunner()

            _verify_signed_efi_chain(runner, target, valid_plan(architecture=self.architecture))

        self.assertEqual(
            [command for command, _kwargs in runner.commands],
            [("chroot", str(target), "/usr/libexec/anduinos-grub-update", "first-install-check")],
        )

    def test_pending_matching_key_makes_retry_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            certificate = target / MOK_CERTIFICATE
            certificate.parent.mkdir(parents=True)
            # Its SHA-1 begins with 01. Leading zeroes are part of a
            # fingerprint and must not be stripped like certificate serials.
            certificate.write_bytes(b"certificate-17")
            prepare_signed_efi_chain(target, self.suffix)
            fingerprint = hashlib.sha1(
                b"certificate-17", usedforsecurity=False
            ).hexdigest()
            self.assertTrue(fingerprint.startswith("0"))
            formatted = ":".join(
                fingerprint[index : index + 2]
                for index in range(0, len(fingerprint), 2)
            )
            runner = FakeRunner()
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--list-new",
                )
            ] = (f"SHA1 Fingerprint: {formatted}\n", "", 0)
            context = InstallContext(
                valid_plan(architecture=self.architecture),
                lambda _message: None,
                {"target": target, "secure_boot_prepared": True},
            )
            EnrollSecureBootStep(runner).execute(context)
        self.assertFalse(
            any("--import" in command for command, _kwargs in runner.commands)
        )

    def test_already_enrolled_key_never_creates_pending_request(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            certificate = target / MOK_CERTIFICATE
            certificate.parent.mkdir(parents=True)
            # Exercise the real-machine failure mode: the enrolled MOK SHA-1
            # begins with 0F and was previously compared after losing that 0.
            certificate.write_bytes(b"certificate-17")
            prepare_signed_efi_chain(target, self.suffix)
            fingerprint = hashlib.sha1(
                b"certificate-17", usedforsecurity=False
            ).hexdigest()
            self.assertTrue(fingerprint.startswith("0"))
            formatted = ":".join(
                fingerprint[index : index + 2]
                for index in range(0, len(fingerprint), 2)
            )
            runner = FakeRunner()
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--list-enrolled",
                )
            ] = (f"SHA1 Fingerprint: {formatted}\n", "", 0)
            context = InstallContext(
                valid_plan(architecture=self.architecture),
                lambda _message: None,
                {"target": target, "secure_boot_prepared": True},
            )
            EnrollSecureBootStep(runner).execute(context)
        self.assertFalse(context.values["mok_enrollment_pending"])
        self.assertFalse(
            any(
                "--import" in command or "--timeout" in command
                for command, _kwargs in runner.commands
            )
        )

    def test_mokutil_072_status_is_not_used_as_enrollment_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            certificate = target / MOK_CERTIFICATE
            certificate.parent.mkdir(parents=True)
            certificate.write_bytes(b"certificate")
            prepare_signed_efi_chain(target, self.suffix)
            runner = FakeRunner()
            runner.outputs[
                ("chroot", str(target), "mokutil", "--list-enrolled")
            ] = ("", "failed", 1)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--test-key",
                    f"/{MOK_CERTIFICATE}",
                )
            ] = (f"/{MOK_CERTIFICATE} is already enrolled\n", "", 1)
            context = InstallContext(
                valid_plan(architecture=self.architecture),
                lambda _message: None,
                {"target": target, "secure_boot_prepared": True},
            )
            EnrollSecureBootStep(runner).execute(context)
        self.assertFalse(context.values["mok_enrollment_pending"])
        self.assertFalse(
            any("--import" in command for command, _kwargs in runner.commands)
        )

    def test_disabled_secure_boot_never_touches_efi_variables(self):
        base = valid_plan(architecture=self.architecture)
        plan = replace(
            base,
            platform=replace(base.platform, secure_boot=SecureBoot.DISABLED),
            boot=BootSpec(
                mok_password_policy=MokPasswordPolicy.NOT_APPLICABLE
            ),
        )
        runner = FakeRunner()
        context = InstallContext(plan, lambda _message: None)
        EnrollSecureBootStep(runner).execute(context)
        self.assertFalse(context.values["mok_enrollment_pending"])
        self.assertEqual(runner.commands, [])

    def test_unsupported_secure_boot_never_touches_efi_variables(self):
        base = valid_plan(architecture=self.architecture)
        plan = replace(
            base,
            platform=replace(
                base.platform, secure_boot=SecureBoot.UNSUPPORTED
            ),
            boot=BootSpec(
                mok_password_policy=MokPasswordPolicy.NOT_APPLICABLE
            ),
        )
        runner = FakeRunner()
        context = InstallContext(plan, lambda _message: None)
        EnrollSecureBootStep(runner).execute(context)
        self.assertFalse(context.values["mok_enrollment_pending"])
        self.assertEqual(runner.commands, [])


class Arm64EnrollSecureBootTests(EnrollSecureBootTests):
    architecture = Architecture.ARM64
    suffix = 'aa64'


class VerifyDkmsSignaturesTests(unittest.TestCase):
    def test_builds_dkms_and_accepts_matching_mok_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / MOK_CERTIFICATE).parent.mkdir(parents=True)
            (target / MOK_CERTIFICATE).write_bytes(b"certificate")
            dkms = target / "usr/sbin/dkms"
            dkms.parent.mkdir(parents=True)
            dkms.touch()
            module = target / "lib/modules/1.0/updates/dkms/example.ko"
            module.parent.mkdir(parents=True)
            module.touch()
            runner = FakeRunner()
            serial_command = (
                "chroot",
                str(target),
                "openssl",
                "x509",
                "-inform",
                "DER",
                "-in",
                f"/{MOK_CERTIFICATE}",
                "-serial",
                "-noout",
            )
            module_command = (
                "chroot",
                str(target),
                "modinfo",
                "-F",
                "sig_key",
                "/lib/modules/1.0/updates/dkms/example.ko",
            )
            runner.outputs[serial_command] = ("serial=00ABCD\n", "", 0)
            runner.outputs[module_command] = ("AB:CD\n", "", 0)
            context = InstallContext(
                valid_plan(), lambda _message: None, {"target": target}
            )
            step = VerifyDkmsSignaturesStep(runner)
            step.execute(context)
            step.verify(context)

        self.assertIn(
            ("chroot", str(target), "dkms", "autoinstall"),
            [item[0] for item in runner.commands],
        )

    def test_rejects_dkms_module_signed_by_another_key(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / MOK_CERTIFICATE).parent.mkdir(parents=True)
            (target / MOK_CERTIFICATE).write_bytes(b"certificate")
            module = target / "lib/modules/1.0/updates/dkms/example.ko"
            module.parent.mkdir(parents=True)
            module.touch()
            runner = FakeRunner()
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "openssl",
                    "x509",
                    "-inform",
                    "DER",
                    "-in",
                    f"/{MOK_CERTIFICATE}",
                    "-serial",
                    "-noout",
                )
            ] = ("serial=ABCD\n", "", 0)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "modinfo",
                    "-F",
                    "sig_key",
                    "/lib/modules/1.0/updates/dkms/example.ko",
                )
            ] = ("DEAD:BEEF\n", "", 0)
            context = InstallContext(
                valid_plan(), lambda _message: None, {"target": target}
            )
            with self.assertRaisesRegex(RuntimeError, "not signed"):
                VerifyDkmsSignaturesStep(runner).verify(context)

    def test_unsigned_chain_fails_before_mok_import(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            certificate = target / MOK_CERTIFICATE
            certificate.parent.mkdir(parents=True)
            certificate.write_bytes(b"certificate")
            prepare_signed_efi_chain(target)
            runner = FakeRunner()
            unsigned = (
                "chroot", str(target),
                "/usr/libexec/anduinos-grub-update", "first-install-check",
            )
            runner.outputs[unsigned] = ("", "No signature", 1)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                {"target": target, "secure_boot_prepared": True},
            )
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                EnrollSecureBootStep(runner).execute(context)
        self.assertFalse(
            any("--import" in command for command, _kwargs in runner.commands)
        )
