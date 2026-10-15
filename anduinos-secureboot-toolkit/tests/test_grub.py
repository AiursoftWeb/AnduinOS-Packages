"""Native signing and failure preservation; firmware/package boundaries mocked."""
from dataclasses import replace
import hashlib
from pathlib import Path
import subprocess
import struct
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from anduinos_secureboot import grub, identity
from anduinos_secureboot.architecture import architecture
from anduinos_secureboot.dkms_guard import validate_signing_defaults
from anduinos_secureboot.model import GrubState, SecureBootStatus


class Runner:
    def __init__(self, arch="amd64"):
        self.arch = arch
        self.calls = []
        self.fail_signing = False
        self.modified = False
        self.owner = "anduinos-grub"
        self.removals = "Remv shim-signed [1.59]\nRemv grub2-common [2.14]\n"

    def run(self, command, timeout=30):
        self.calls.append(command)
        if command == ["dpkg", "--print-architecture"]:
            value = self.arch
        elif command[0] == "dpkg-query":
            value = f"{self.owner}: {command[-1]}" if "--search" in command else "installed"
        elif command[:2] == ["dpkg", "--verify"]:
            value = f"??5?????? {self.calls[-2][-1]}" if self.modified else ""
        elif command[:2] == ["apt-get", "--simulate"]:
            value = self.removals
        elif command[0] == "apt-get":
            value = "Packages installed"
        elif command[0] == "sbsign" and self.fail_signing:
            return subprocess.CompletedProcess(command, 1, "", "Injected signing failure")
        else:
            return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return subprocess.CompletedProcess(command, 0, value, "")


class GrubArchitectureTests(unittest.TestCase):
    def test_inspection_depends_on_architecture_not_release_codename(self):
        for arch in ("amd64", "arm64", "riscv64", ""):
            with self.subTest(architecture=arch):
                runner = Mock()
                runner.run.return_value = subprocess.CompletedProcess([], 0, arch, "")
                with (patch.object(grub, "packages_installed", return_value=False) as packages,
                      patch.object(Path, "read_text", side_effect=AssertionError("No release gate"))):
                    state = grub.inspect_grub(runner)
                self.assertEqual(state.supported, arch in {"amd64", "arm64"})
                self.assertEqual(packages.called, arch in {"amd64", "arm64"})


class GrubSigningTests:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.key, self.cert = self.root / "key", self.root / "cert.der"
        self.runner = Runner(self.arch)
        identity.generate_identity(self.key, self.cert, self.runner.run)
        code = self.root / "efi.c"
        code.write_text("int efi_main(void *image, void *table) { return 0; }\n")
        obj = self.root / "efi.o"
        self.source = self.root / "grub.efi"
        target = {"amd64": "x86_64", "arm64": "aarch64"}[self.arch]
        subprocess.run(["clang", f"--target={target}-pc-windows-msvc", "-ffreestanding",
                        "-fno-stack-protector", "-c", str(code), "-o", str(obj)], check=True, capture_output=True)
        subprocess.run(["lld-link", "/subsystem:efi_application", "/entry:efi_main", "/nodefaultlib",
                        f"/out:{self.source}", str(obj)], check=True, capture_output=True)
        self.source.chmod(0o644)
        self.store = self.root / "store"
        self.firmware = SimpleNamespace(status=SecureBootStatus.ENABLED)
        for target, value in (("probe_firmware", self.firmware),):
            mock = patch.object(grub, target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        selected = patch.object(identity, "selected_paths", return_value=(self.key, self.cert))
        selected.start()
        self.addCleanup(selected.stop)
        self.enrollment = patch.object(identity, "enrollment_state", return_value="enrolled").start()
        self.addCleanup(patch.stopall)
        compatibility = patch.object(grub, "_compatibility_image")
        compatibility.start()
        self.addCleanup(compatibility.stop)

    def prepare(self, **kwargs):
        return grub.prepare_image(self.runner, source=self.source, store=self.store, **kwargs)

    def test_real_efi_signature_uses_the_unified_certificate_and_retry_is_stable(self):
        image = self.prepare()
        grub.verify_signature(image, self.cert, self.runner)
        before = image.read_bytes()
        signing_calls = sum(command[0] == "sbsign" for command in self.runner.calls)
        self.assertEqual(self.prepare().read_bytes(), before)
        self.assertEqual(signing_calls, sum(command[0] == "sbsign" for command in self.runner.calls))

    def test_wrong_architecture_signature_is_rejected(self):
        image = self.prepare()
        self.runner.arch = "arm64" if self.arch == "amd64" else "amd64"
        with self.assertRaises(ValueError):
            grub.verify_signature(image, self.cert, self.runner)

    def test_tampered_signature_is_rejected(self):
        image = self.prepare()
        data = bytearray(image.read_bytes())
        # Locate .text; compiler-dependent file alignment is not a contract.
        pe = struct.unpack_from("<I", data, 0x3c)[0]
        optional_size = struct.unpack_from("<H", data, pe + 20)[0]
        section = pe + 24 + optional_size
        self.assertEqual(data[section:section + 5], b".text")
        text_offset = struct.unpack_from("<I", data, section + 20)[0]
        data[text_offset] ^= 1
        image.write_bytes(data)
        with self.assertRaises(ValueError):
            grub.verify_signature(image, self.cert, self.runner)

    def test_failed_signing_retains_the_previous_image_and_certificate(self):
        image = self.prepare()
        before = {path.name: path.read_bytes() for path in self.store.iterdir() if path.is_file()}
        # A new pristine package image needs signing; inject that boundary's
        # failure and ensure no successfully prepared artifact is overwritten.
        self.source.write_bytes(self.source.read_bytes() + b"new version")
        self.runner.fail_signing = True
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.store.iterdir() if path.is_file()})
        self.assertTrue(image.is_file())

    def test_missing_private_key_never_recreates_or_changes_certificate(self):
        before = self.cert.read_bytes()
        self.key.unlink()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(before, self.cert.read_bytes())
        self.assertFalse(self.key.exists())
        self.assertFalse(self.store.exists())

    def test_mismatched_pair_stops_before_signing(self):
        other_key, other_cert = self.root / "other.key", self.root / "other.der"
        identity.generate_identity(other_key, other_cert, self.runner.run)
        self.cert.write_bytes(other_cert.read_bytes())
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(any(command[0] == "sbsign" for command in self.runner.calls))

    def test_unknown_firmware_or_incomplete_enrollment_cannot_replace_active_chain(self):
        self.firmware.status = SecureBootStatus.UNKNOWN
        with self.assertRaises(ValueError):
            self.prepare()
        self.firmware.status = SecureBootStatus.ENABLED
        for enrollment in ("absent", "pending", "unknown"):
            with self.subTest(enrollment=enrollment):
                self.enrollment.return_value = enrollment
                with self.assertRaises(ValueError):
                    self.prepare()
        self.assertFalse(self.store.exists())

    def test_first_install_can_prepare_signed_image_before_enrollment(self):
        self.enrollment.return_value = "absent"
        image = self.prepare(first_install=True)
        grub.verify_signature(image, self.cert, self.runner)

    def test_modified_or_wrong_package_payload_is_not_signed(self):
        for field, value in (("modified", True), ("owner", "other-package")):
            with self.subTest(field=field):
                setattr(self.runner, field, value)
                with self.assertRaises(ValueError):
                    self.prepare()
                setattr(self.runner, field, False if field == "modified" else "anduinos-grub")
        self.assertFalse(any(command[0] == "sbsign" for command in self.runner.calls))

    def test_package_replacement_cannot_remove_desktop_or_kernel(self):
        with patch.object(grub.boot_chain, "resolve_target"):
            for package in ("anduinos-desktop", "linux-generic-hwe-26.04", "nvidia-driver-595"):
                with self.subTest(package=package):
                    self.runner.removals = f"Remv {package} [1.0]\n"
                    with self.assertRaises(ValueError):
                        grub.install_packages(self.runner.run)
            self.assertFalse(any(command[:2] == ["apt-get", "-y"] for command in self.runner.calls))

    def test_allowed_replacement_runs_after_trust_check(self):
        with patch.object(grub.boot_chain, "resolve_target"):
            result = grub.install_packages(self.runner.run)
        self.assertTrue(result.ok)
        transaction = next(command for command in self.runner.calls if command[:2] == ["apt-get", "-y"])
        self.assertIn(f"grub-efi-{self.arch}-", transaction)
        self.assertEqual("grub-pc-" in transaction, bool(architecture(self.arch).bios_platform))
        other = "arm64" if self.arch == "amd64" else "amd64"
        self.assertNotIn(f"grub-efi-{other}-", transaction)

    def test_package_logs_keep_both_streams_and_failure_status(self):
        for code in (0, 1):
            with self.subTest(code=code), patch.object(grub.boot_chain, "resolve_target"):
                def run(command, **kwargs):
                    if command[:2] == ["apt-get", "-y"]:
                        return subprocess.CompletedProcess(command, code, "package output\n", "hook diagnostic\n")
                    return self.runner.run(command, **kwargs)
                result = grub.install_packages(run)
                self.assertEqual(result.ok, code == 0)
                self.assertEqual(result.steps["packages"].detail, "package output\nhook diagnostic")

    def test_dkms_missing_pair_is_rejected_before_upstream_can_generate_keys(self):
        config = self.root / "etc/dkms/framework.conf.d/anduinos.conf"
        config.parent.mkdir(parents=True)
        for directory in (config.parent, *config.parent.parents):
            if directory == self.root:
                break
            directory.chmod(0o755)
        config.write_text(f'mok_signing_key="{self.key}"\nmok_certificate="{self.cert}"\n')
        config.chmod(0o644)
        validate_signing_defaults(["build", "-m", "fixture"], self.runner, config)
        before = self.cert.read_bytes()
        self.key.unlink()
        with self.assertRaises(ValueError):
            validate_signing_defaults(["autoinstall"], self.runner, config)
        self.assertEqual(before, self.cert.read_bytes())
        self.assertFalse(self.key.exists())
        # Listing/removing existing modules remains usable for diagnosis.
        validate_signing_defaults(["status"], self.runner, config)
        validate_signing_defaults(["remove"], self.runner, config)

    def test_dkms_missing_certificate_and_explicit_generate_are_rejected(self):
        config = self.root / "config"
        config.write_text(f'mok_signing_key="{self.key}"\nmok_certificate="{self.cert}"\n')
        self.cert.unlink()
        with self.assertRaises(ValueError):
            validate_signing_defaults(["install"], self.runner, config)
        self.assertFalse(self.cert.exists())
        with self.assertRaises(ValueError):
            validate_signing_defaults(["generate_mok"], self.runner, config)

    def test_signature_enrollment_and_deployment_are_independent(self):
        ready = GrubState(installed=True, signature="valid", enrollment="enrolled", deployment="ready")
        self.assertTrue(ready.ready)
        for field, value in (("installed", False), ("signature", "invalid"),
                             ("enrollment", "pending"), ("deployment", "pending"), ("deployment", "unknown")):
            self.assertFalse(replace(ready, **{field: value}).ready)


class Amd64GrubSigningTests(GrubSigningTests, unittest.TestCase):
    arch = "amd64"


class Arm64GrubSigningTests(GrubSigningTests, unittest.TestCase):
    arch = "arm64"
