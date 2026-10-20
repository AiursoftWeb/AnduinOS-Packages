"""Native signing and failure preservation; firmware/package boundaries mocked."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from anduinos_secureboot import grub, identity
from anduinos_secureboot.dkms_guard import validate_signing_defaults
from anduinos_secureboot.model import GrubState, SecureBootStatus


class Runner:
    def __init__(self, architecture="amd64"):
        self.architecture = architecture
        self.calls = []
        self.fail_signing = False
        self.modified = False
        self.owner = "anduinos-grub"
        self.owners = {}
        self.removals = "Remv shim-signed [1.59]\nRemv grub2-common [2.14]\n"

    def run(self, command, timeout=30):
        self.calls.append(command)
        if command == ["dpkg", "--print-architecture"]:
            value = self.architecture
        elif command[0] == "dpkg-query":
            value = f"{self.owners.get(command[-1], self.owner)}: {command[-1]}" if "--search" in command else "installed"
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
                self.assertEqual(state.supported, arch in ("amd64", "arm64"))
                self.assertEqual(packages.called, arch in ("amd64", "arm64"))

    def test_payload_names_and_replacement_packages_match_native_architecture(self):
        for arch, suffix, platform, machine in (("amd64", "x64", "x86_64", 0x8664),
                                                ("arm64", "aa64", "arm64", 0xAA64)):
            with self.subTest(architecture=arch):
                runner = Runner(arch)
                self.assertEqual(grub.boot_chain.efi_architecture(runner), (suffix, platform, machine))
                self.assertEqual(grub.source_image(runner).name, f"grub{suffix}.efi")
                self.assertEqual(set(grub.shim_companions(runner)), {f"shim{suffix}.efi", f"mm{suffix}.efi"})
                packages = grub.old_packages(runner)
                self.assertIn(f"grub-efi-{arch}-signed", packages)
                other = "arm64" if arch == "amd64" else "amd64"
                self.assertFalse(any(f"grub-efi-{other}" in name for name in packages))
                self.assertEqual("grub-pc-bin" in packages, arch == "amd64")


class GrubSigningTests(unittest.TestCase):
    architecture = "amd64"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.key, self.cert = self.root / "key", self.root / "cert.der"
        self.runner = Runner(self.architecture)
        identity.generate_identity(self.key, self.cert, self.runner.run)
        suffix, _, machine = grub.boot_chain.efi_architecture(self.runner)
        self.source = self.root / f"grub{suffix}.efi"
        # A minimal PE32+ signing fixture, never executed. Build its metadata
        # directly so BOTH ISA tests run on either host without cross compilers.
        image = bytearray(1024)
        image[:2] = b"MZ"
        struct.pack_into("<I", image, 60, 128)
        image[128:132] = b"PE\0\0"
        struct.pack_into("<HHIIIHH", image, 132, machine, 1, 0, 0, 0, 240, 0x22)
        optional = 152
        struct.pack_into("<H", image, optional, 0x20B)
        struct.pack_into("<I", image, optional + 4, 512)  # SizeOfCode
        struct.pack_into("<II", image, optional + 16, 4096, 4096)
        struct.pack_into("<II", image, optional + 32, 4096, 512)
        struct.pack_into("<II", image, optional + 56, 8192, 512)
        struct.pack_into("<H", image, optional + 68, 10)  # EFI application
        struct.pack_into("<I", image, optional + 108, 16)
        section = optional + 240
        image[section:section + 8] = b".text\0\0\0"
        struct.pack_into("<IIII", image, section + 8, 8, 4096, 512, 512)
        struct.pack_into("<I", image, section + 36, 0x60000020)
        self.source.write_bytes(image)
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
        self.compatibility = patch.object(grub, "_compatibility_image")
        self.compatibility.start()
        self.addCleanup(self.compatibility.stop)

    def prepare(self, **kwargs):
        return grub.prepare_image(self.runner, source=self.source, store=self.store, **kwargs)

    def test_real_efi_signature_uses_the_unified_certificate_and_retry_is_stable(self):
        image = self.prepare()
        grub.verify_signature(image, self.cert, self.runner)
        before = image.read_bytes()
        signing_calls = sum(command[0] == "sbsign" for command in self.runner.calls)
        self.assertEqual(self.prepare().read_bytes(), before)
        self.assertEqual(signing_calls, sum(command[0] == "sbsign" for command in self.runner.calls))

    def test_first_install_checks_final_files_without_signing_or_enrollment(self):
        # Model the installer's protected ESP, independent of the CI umask.
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        image = self.prepare()
        suffix, _, machine = grub.boot_chain.efi_architecture(self.runner)
        vendor = self.root / "esp/EFI/AnduinOS"
        vendor.mkdir(parents=True)
        companions = {}
        for name in (f"shim{suffix}.efi", f"mm{suffix}.efi"):
            source = self.root / name
            source.write_bytes(image.read_bytes())
            companions[name] = source
            self.runner.owners[str(source)] = "anduinos-shim-signed"
        original = {image.name: image.read_bytes(),
                    **{name: source.read_bytes() for name, source in companions.items()}}
        for name, data in original.items():
            (vendor / name).write_bytes(data)
        before_identity = self.key.read_bytes(), self.cert.read_bytes()
        other_key, other_cert = self.root / "other.key", self.root / "other.der"
        identity.generate_identity(other_key, other_cert, self.runner.run)
        pem = self.root / "other.pem"
        self.runner.run(["openssl", "x509", "-inform", "DER", "-in", str(other_cert), "-out", str(pem)])
        wrong_signer = self.root / "wrong-signer.efi"
        self.runner.run(["sbsign", "--key", str(other_key), "--cert", str(pem),
                         "--output", str(wrong_signer), str(self.source)])
        damaged = bytearray(image.read_bytes())
        damaged[0x200] ^= 1
        other_arch = bytearray(image.read_bytes())
        struct.pack_into("<H", other_arch, 132, 0xAA64 if machine == 0x8664 else 0x8664)
        cases = (
            (image.name, bytes(damaged)),
            (image.name, wrong_signer.read_bytes()),
            (image.name, self.source.read_bytes()),
            (image.name, bytes(other_arch)),
            (f"shim{suffix}.efi", wrong_signer.read_bytes()),
            (f"mm{suffix}.efi", bytes(damaged)),
            (f"mm{suffix}.efi", None),
        )
        with (patch.object(grub, "source_image", return_value=self.source),
              patch.object(grub, "shim_companions", return_value=companions),
              patch.object(identity, "LEGACY_CERTIFICATE", self.cert),
              patch.object(grub, "prepare_image", side_effect=AssertionError("Read-only verification")),
              patch.object(grub.boot_chain, "resolve_target", side_effect=AssertionError("No Live BootCurrent"))):
            self.enrollment.return_value = "absent"
            grub.verify_first_install(self.runner, vendor.parent.parent)
            self.assertEqual(original, {p.name: p.read_bytes() for p in vendor.iterdir()})
            for name, data in cases:
                with self.subTest(name=name, data=None if data is None else len(data)):
                    destination = vendor / name
                    if data is None:
                        destination.unlink()
                    else:
                        destination.write_bytes(data)
                    with self.assertRaises((ValueError, OSError)):
                        grub.verify_first_install(self.runner, vendor.parent.parent)
                    destination.write_bytes(original[name])
            self.runner.modified = True
            with self.assertRaisesRegex(ValueError, "modified"):
                grub.verify_first_install(self.runner, vendor.parent.parent)
            self.runner.modified = False
            self.source.unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                grub.verify_first_install(self.runner, vendor.parent.parent)
        self.assertEqual(before_identity, (self.key.read_bytes(), self.cert.read_bytes()))

    def test_derived_cache_corruption_is_rebuilt_from_the_same_identity(self):
        image = self.prepare()
        original = {p.name: p.read_bytes() for p in self.store.iterdir()}
        authority = self.key.read_bytes(), self.cert.read_bytes(), self.source.read_bytes()
        for fault in ("json", "array", "certificate", "image", "invalid-signature"):
            with self.subTest(fault=fault):
                for name, data in original.items():
                    (self.store / name).write_bytes(data)
                manifest = self.store / "image.json"
                if fault == "json":
                    manifest.write_text("{broken")
                elif fault == "array":
                    manifest.write_text("[]")
                elif fault == "certificate":
                    (self.store / "certificate.der").write_bytes(b"broken certificate")
                else:
                    image.write_bytes(self.source.read_bytes())
                    if fault == "invalid-signature":
                        # Matching hashes alone cannot make an unsigned PE valid.
                        recorded = json.loads(manifest.read_text())
                        recorded["image_sha256"] = grub.digest(image)
                        manifest.write_text(json.dumps(recorded))
                calls = sum(c[0] == "sbsign" for c in self.runner.calls)
                self.assertEqual(self.prepare(), image)
                self.assertEqual(sum(c[0] == "sbsign" for c in self.runner.calls), calls + 1)
                grub.verify_signature(image, self.cert, self.runner)
                self.assertEqual((self.store / "certificate.der").read_bytes(), self.cert.read_bytes())
                self.assertEqual(json.loads(manifest.read_text())["image_sha256"], grub.digest(image))
                self.assertEqual(authority, (self.key.read_bytes(), self.cert.read_bytes(), self.source.read_bytes()))

    def test_non_object_cache_metadata_reports_pending_without_crashing(self):
        self.prepare()
        (self.store / "image.json").write_text("[]")
        with (patch.object(grub, "STORE", self.store),
              patch.object(grub, "source_image", return_value=self.source)):
            state = grub.inspect_grub(self.runner, privileged=True)
        self.assertEqual(state.deployment, "pending")
        self.assertFalse(state.ready)

    def test_cache_rebuild_signing_failure_preserves_existing_files(self):
        self.prepare()
        (self.store / "image.json").write_text("{broken")
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        self.runner.fail_signing = True
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})

    def test_tampered_signature_is_rejected(self):
        image = self.prepare()
        data = bytearray(image.read_bytes())
        # Alter executable contents rather than the PE checksum (not covered
        # by Authenticode). The fixture's .text section starts at offset 0x200.
        data[0x200] ^= 1
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

    def test_compatibility_copy_uses_the_native_grub_directory(self):
        image = self.prepare()
        self.compatibility.stop()
        suffix, platform, _ = grub.boot_chain.efi_architecture(self.runner)
        destination = self.root / "compatibility"
        with patch.object(grub, "Path", return_value=destination) as path:
            grub._compatibility_image(image, self.runner)
        path.assert_called_once_with(f"/usr/lib/grub/{platform}-efi-signed")
        self.assertEqual((destination / f"grub{suffix}.efi.signed").read_bytes(), image.read_bytes())

    def test_wrong_architecture_and_wrong_certificate_are_rejected(self):
        image = self.prepare()
        self.runner.architecture = "arm64" if self.architecture == "amd64" else "amd64"
        with self.assertRaisesRegex(ValueError, "architecture"):
            grub.verify_signature(image, self.cert, self.runner)
        self.runner.architecture = self.architecture
        other_key, other_cert = self.root / "other.key", self.root / "other.der"
        identity.generate_identity(other_key, other_cert, self.runner.run)
        with self.assertRaises(ValueError):
            grub.verify_signature(image, other_cert, self.runner)

    def test_updated_template_and_certificate_are_resigned(self):
        image = self.prepare()
        before = image.read_bytes()
        data = bytearray(self.source.read_bytes())
        data[0x200] ^= 1
        self.source.write_bytes(data)
        self.assertNotEqual(self.prepare().read_bytes(), before)
        grub.verify_signature(image, self.cert, self.runner)
        other_key, other_cert = self.root / "other.key", self.root / "other.der"
        identity.generate_identity(other_key, other_cert, self.runner.run)
        self.key.write_bytes(other_key.read_bytes())
        self.cert.write_bytes(other_cert.read_bytes())
        self.prepare()
        grub.verify_signature(image, self.cert, self.runner)

    def test_inspection_distinguishes_prepared_update_from_deployed_image(self):
        image = self.prepare()
        suffix, _, machine = grub.boot_chain.efi_architecture(self.runner)
        esp = self.root / "esp"
        vendor = esp / "EFI/AnduinOS"
        vendor.mkdir(parents=True)
        deployed = vendor / image.name
        deployed.write_bytes(image.read_bytes())
        companions = {}
        for name in (f"shim{suffix}.efi", f"mm{suffix}.efi"):
            source = self.root / name
            source.write_bytes(image.read_bytes())
            (vendor / name).write_bytes(source.read_bytes())
            companions[name] = source
        target = SimpleNamespace(entry=SimpleNamespace(loader=rf"\EFI\AnduinOS\shim{suffix}.efi"))
        # Keep package/firmware boundaries isolated; signatures, state manifests
        # and file comparisons use the real fixture files and signing tools.
        with (patch.object(grub, "STORE", self.store),
              patch.object(grub, "source_image", return_value=self.source),
              patch.object(grub, "shim_companions", return_value=companions),
              patch.object(grub, "package_payload"),
              patch.object(grub.boot_chain, "resolve_target", return_value=target)):
            self.assertTrue(grub.inspect_grub(self.runner, privileged=True, esp=esp).ready)
            with patch.object(grub, "prepare_image", return_value=image):
                sources, actual_machine = grub.payloads(self.runner)
            self.assertEqual(actual_machine, machine)
            self.assertEqual(set(sources), {image.name, *companions})
            data = bytearray(self.source.read_bytes())
            data[0x200] ^= 1
            self.source.write_bytes(data)
            self.assertEqual(grub.inspect_grub(self.runner, privileged=True, esp=esp).deployment, "pending")
            self.prepare()
            self.assertFalse(grub.inspect_grub(self.runner, privileged=True, esp=esp).ready)
            deployed.write_bytes(image.read_bytes())
            self.assertTrue(grub.inspect_grub(self.runner, privileged=True, esp=esp).ready)
            fallback = esp / "EFI/BOOT"
            fallback.mkdir()
            for name, source in {image.name: image, **companions}.items():
                destination = f"BOOT{suffix.upper()}.EFI" if name.startswith("shim") else name
                (fallback / destination).write_bytes(source.read_bytes())
            with patch.object(grub.boot_chain, "owned_fallback", return_value=fallback):
                self.assertTrue(grub.inspect_grub(self.runner, privileged=True, esp=esp).ready)
                (fallback / image.name).write_bytes(b"stale portable copy")
                state = grub.inspect_grub(self.runner, privileged=True, esp=esp)
                self.assertEqual(state.deployment, "pending")
                self.assertIn("portable", state.detail)
            other = "aa64" if suffix == "x64" else "x64"
            target.entry.loader = rf"\EFI\AnduinOS\shim{other}.efi"
            state = grub.inspect_grub(self.runner, privileged=True, esp=esp)
            self.assertFalse(state.ready)
            self.assertIn("architecture", state.detail)

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

    def test_replacement_cannot_remove_other_architecture_boot_packages(self):
        other = "arm64" if self.architecture == "amd64" else "amd64"
        self.runner.removals = f"Remv grub-efi-{other}-signed [1.0]\n"
        with patch.object(grub.boot_chain, "resolve_target"):
            with self.assertRaisesRegex(ValueError, "unrelated"):
                grub.install_packages(self.runner.run)
        self.assertFalse(any(command[:2] == ["apt-get", "-y"] for command in self.runner.calls))

    def test_allowed_replacement_runs_after_trust_check(self):
        with patch.object(grub.boot_chain, "resolve_target"):
            result = grub.install_packages(self.runner.run)
        self.assertTrue(result.ok)
        self.assertTrue(any(command[:2] == ["apt-get", "-y"] for command in self.runner.calls))

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


class Arm64GrubSigningTests(GrubSigningTests):
    architecture = "arm64"
