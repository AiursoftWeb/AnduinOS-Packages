"""Run the Python consumer against signed output from the C# publisher's tests."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from anduinos_offline_updater.bundle import verify
from anduinos_offline_updater.native import BundleError
from anduinos_offline_updater.planning import preview


class PublisherContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        export = os.environ.get("OFFLINE_CONTRACT_EXPORT")
        if not export:
            raise RuntimeError("Set OFFLINE_CONTRACT_EXPORT to the publisher's fixture output directory")
        cls.fixtures = Path(export).resolve()
        cls.keyring = cls.fixtures / "trusted.gpg"

    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix="offline-publisher-contract-")
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.system = self.root / "system"
        self.status = self.system / "var/lib/dpkg/status"
        self.release = self.system / "usr/lib/os-release"
        self.status.parent.mkdir(parents=True)
        self.release.parent.mkdir(parents=True)
        shutil.copyfile(self.fixtures / "baseline.status", self.status)
        shutil.copyfile(self.fixtures / "baseline.os-release", self.release)
        self.arch = self.status.with_name("arch")
        self.arch.write_text("amd64\n")

    def inspect(self, name, **options):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            metadata = verify(self.fixtures / name, self.keyring, directory)
            result = preview(metadata["manifest"], Path(directory) / "repository", self.keyring,
                             self.system, self.root, **options)
        self.assertEqual(self.status.read_bytes(), (self.fixtures / "baseline.status").read_bytes())
        return result

    def test_legacy_bundle_plans_system_update(self):
        result = self.inspect("legacy.atgz")
        self.assertEqual(result["state"], "update-available", result["blockers"])
        self.assertTrue(any(stage["changes"] for stage in result["stages"]))

    def test_old_client_plans_only_updater_and_its_dependencies_first(self):
        result = self.inspect("bootstrap.atgz", client_version="2.1.0-1")
        self.assertEqual(result["state"], "updater-update-required", result["blockers"])
        packages = {change["name"] for stage in result["stages"] for change in stage["changes"]}
        self.assertIn("anduinos-offline-updater", packages)
        self.assertIn("bootstrap-library", packages)
        self.assertNotIn("anduinos-core-system", packages)

    def test_current_client_plans_system_update(self):
        result = self.inspect("bootstrap.atgz")
        self.assertEqual(result["state"], "update-available", result["blockers"])
        self.assertTrue(any(change["name"] == "anduinos-core-system"
                            for stage in result["stages"] for change in stage["changes"]))

    def test_future_feature_verifies_but_blocks_planning(self):
        result = self.inspect("future.atgz")
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["stages"], [])
        self.assertIn("future-installer-v2", " ".join(result["blockers"]))

    def test_unsatisfied_signed_minimum_fails_verification(self):
        with self.assertRaisesRegex(BundleError, "minimum version"):
            verify(self.fixtures / "invalid-minimum.atgz", self.keyring, self.root / "invalid")

    def test_incompatible_machine_cannot_plan_a_verified_update(self):
        for kind, filename, original, replacement in (
            ("suite", self.release, "resolute", "stonking"),
            ("architecture", self.arch, "amd64", "arm64"),
            ("downgrade", self.release, "2.0.0", "2.0.9"),
        ):
            with self.subTest(kind=kind):
                contents = filename.read_text()
                self.assertIn(original, contents)
                filename.write_text(contents.replace(original, replacement))
                try:
                    result = self.inspect("legacy.atgz")
                    self.assertEqual(result["state"], "blocked")
                    self.assertEqual(result["stages"], [])
                    self.assertTrue(result["blockers"])
                finally:
                    filename.write_text(contents)

    def test_truncated_publisher_output_fails_verification(self):
        data = (self.fixtures / "legacy.atgz").read_bytes()
        truncated = self.root / "incomplete.atgz"
        truncated.write_bytes(data[:len(data) // 2])
        with self.assertRaises(BundleError):
            verify(truncated, self.keyring, self.root / "invalid")

    def test_publisher_output_needs_its_trusted_key(self):
        with self.assertRaises(BundleError):
            verify(self.fixtures / "legacy.atgz", self.root / "missing.gpg", self.root / "invalid")


if __name__ == "__main__":
    unittest.main()
