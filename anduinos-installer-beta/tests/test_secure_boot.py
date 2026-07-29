import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.model import (
    Architecture,
    BootSpec,
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
)
from installer_core.steps import InstallContext


PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----"


class KeyGeneratingRunner(FakeRunner):
    def __init__(self, target: Path):
        super().__init__()
        self.target = target

    def run(self, command, **kwargs):
        result = super().run(command, **kwargs)
        if tuple(command)[-2:] == ("update-secureboot-policy", "--new-key"):
            private = self.target / MOK_PRIVATE_KEY
            certificate = self.target / MOK_CERTIFICATE
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_bytes(b"new-private")
            certificate.write_bytes(b"new-certificate")
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
        "usr/sbin/update-secureboot-policy",
        "usr/bin/mokutil",
        "usr/bin/openssl",
        *signed,
    ):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def prepare_signed_efi_chain(target: Path) -> None:
    for relative in (
        "boot/efi/EFI/BOOT/BOOTX64.EFI",
        "boot/efi/EFI/AnduinOS/shimx64.efi",
        "boot/efi/EFI/AnduinOS/grubx64.efi",
    ):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def configure_key_outputs(runner: FakeRunner, target: Path) -> None:
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
            "-pubkey",
            "-noout",
        )
    ] = (PUBLIC_KEY, "", 0)
    runner.outputs[
        (
            "chroot",
            str(target),
            "openssl",
            "pkey",
            "-in",
            f"/{MOK_PRIVATE_KEY}",
            "-pubout",
        )
    ] = (PUBLIC_KEY, "", 0)


class PrepareSecureBootTests(unittest.TestCase):
    def test_replaces_unmarked_live_key_and_configures_dkms(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target)
            private = target / MOK_PRIVATE_KEY
            certificate = target / MOK_CERTIFICATE
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_bytes(b"live-private")
            certificate.write_bytes(b"live-certificate")
            runner = KeyGeneratingRunner(target)
            configure_key_outputs(runner, target)
            context = InstallContext(
                valid_plan(), lambda _message: None, {"target": target}
            )
            step = PrepareSecureBootStep(runner)
            step.execute(context)
            step.verify(context)

            self.assertEqual(private.read_bytes(), b"new-private")
            self.assertEqual(certificate.read_bytes(), b"new-certificate")
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
            marker = (target / MOK_MARKER).read_text().strip()
            self.assertEqual(
                marker, hashlib.sha256(b"new-certificate").hexdigest()
            )
            dkms = (
                target
                / "etc/dkms/framework.conf.d/anduinos-sb-sign.conf"
            ).read_text()
            self.assertIn("MOK.priv", dkms)
            self.assertIn("MOK.der", dkms)

        self.assertIn(
            (
                "chroot",
                str(target),
                "update-secureboot-policy",
                "--new-key",
            ),
            [item[0] for item in runner.commands],
        )

    def test_secure_boot_disabled_does_nothing(self):
        base = valid_plan()
        plan = replace(
            base,
            platform=replace(base.platform, secure_boot=SecureBoot.DISABLED),
            boot=BootSpec(
                mok_password_policy=MokPasswordPolicy.NOT_APPLICABLE
            ),
        )
        runner = FakeRunner()
        context = InstallContext(plan, lambda _message: None)
        PrepareSecureBootStep(runner).execute(context)
        self.assertFalse(context.values["secure_boot_prepared"])
        self.assertEqual(runner.commands, [])

    def test_arm64_requires_and_accepts_arm64_signed_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_secure_boot_target(target, Architecture.ARM64)
            runner = KeyGeneratingRunner(target)
            configure_key_outputs(runner, target)
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
            prepare_secure_boot_target(target)
            (
                target
                / "usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed"
            ).unlink()
            runner = KeyGeneratingRunner(target)
            context = InstallContext(
                valid_plan(), lambda _message: None, {"target": target}
            )
            with self.assertRaisesRegex(RuntimeError, "Signed.*missing"):
                PrepareSecureBootStep(runner).execute(context)
        self.assertFalse(
            any(
                command[-2:] == ("update-secureboot-policy", "--new-key")
                for command, _kwargs in runner.commands
            )
        )


class EnrollSecureBootTests(unittest.TestCase):
    def test_password_is_only_sent_on_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            certificate = target / MOK_CERTIFICATE
            certificate.parent.mkdir(parents=True)
            certificate.write_bytes(b"certificate")
            prepare_signed_efi_chain(target)
            runner = FakeRunner()
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--test-key",
                    f"/{MOK_CERTIFICATE}",
                )
            ] = ("not enrolled", "", 1)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--list-new",
                    "--short",
                )
            ] = ("", "", 0)
            context = InstallContext(
                valid_plan(),
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

    def test_pending_matching_key_makes_retry_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            certificate = target / MOK_CERTIFICATE
            certificate.parent.mkdir(parents=True)
            certificate.write_bytes(b"certificate")
            prepare_signed_efi_chain(target)
            fingerprint = hashlib.sha1(
                b"certificate", usedforsecurity=False
            ).hexdigest()
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
                    "--test-key",
                    f"/{MOK_CERTIFICATE}",
                )
            ] = ("not enrolled", "", 1)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--list-new",
                    "--short",
                )
            ] = (f"SHA1 Fingerprint: {formatted}\n", "", 0)
            context = InstallContext(
                valid_plan(),
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
            certificate.write_bytes(b"certificate")
            prepare_signed_efi_chain(target)
            runner = FakeRunner()
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "mokutil",
                    "--test-key",
                    f"/{MOK_CERTIFICATE}",
                )
            ] = ("already enrolled", "", 0)
            context = InstallContext(
                valid_plan(),
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

    def test_disabled_secure_boot_never_touches_efi_variables(self):
        base = valid_plan()
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
                "sbverify",
                "--list",
                str(target / "boot/efi/EFI/BOOT/BOOTX64.EFI"),
            )
            runner.outputs[unsigned] = ("", "No signature", 1)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                {"target": target, "secure_boot_prepared": True},
            )
            with self.assertRaisesRegex(RuntimeError, "not signed"):
                EnrollSecureBootStep(runner).execute(context)
        self.assertFalse(
            any("--import" in command for command, _kwargs in runner.commands)
        )
