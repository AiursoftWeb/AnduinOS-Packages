"""Installation safety checks; these tests never call APT commit on the host."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import apt_pkg

from anduinos_offline_updater import jobs
from anduinos_offline_updater.install import check_targets, snapshot_result, space_check
from anduinos_offline_updater.native import BundleError


class InstallationChecks(unittest.TestCase):
    def test_targets_are_checked_even_if_os_version_already_matches(self):
        apt_pkg.init_config(); apt_pkg.init_system()
        manifest = {"version_package": "base-files", "allowed_removals": [], "required_installed_targets": [
            {"name": "base-files", "architecture": "all", "version": "2.0.5"},
            {"name": "vim", "architecture": "amd64", "version": "5"}]}
        initial = {("base-files", "all"): {"Version": "2.0.0"}, ("vim", "amd64"): {"Version": "1"}}
        final = {("base-files", "all"): {"Version": "2.0.5"}}
        with self.assertRaisesRegex(BundleError, "vim"):
            check_targets(manifest, initial, final)
        final[("vim", "amd64")] = {"Version": "1"}
        with self.assertRaisesRegex(BundleError, "vim"):
            check_targets(manifest, initial, final)
        final[("vim", "amd64")] = {"Version": "6"}
        check_targets(manifest, initial, final)
        manifest["required_installed_targets"][0]["architecture"] = "amd64"
        final[("base-files", "all")] = {"Version": "2.0.0"}
        with self.assertRaisesRegex(BundleError, "base-files"):
            check_targets(manifest, initial, final)
        final.pop(("base-files", "all"))
        final[("base-files", "amd64")] = {"Version": "2.0.5"}
        check_targets(manifest, initial, final)

    def test_bootstrap_and_self_replacement_cannot_start(self):
        for report in ({"state": "updater-update-required"}, {"state": "blocked", "blockers": ["Held package"]},
                       {"state": "update-available", "stages": [{"changes": [{"name": "anduinos-offline-updater"}]}]}):
            with self.assertRaises(BundleError): jobs.ensure_installable(report)
        jobs.ensure_installable({"state": "already-installed", "stages": [{"changes": []}]})

    def test_no_snapshot_is_not_an_installation_blocker(self):
        with patch("anduinos_offline_updater.install.snapshot_records", return_value=set()):
            self.assertEqual(snapshot_result(set(), {"state": "unavailable"})["state"], "unavailable")
            self.assertEqual(snapshot_result(set(), {"state": "requires-layout-check"})["state"], "unconfirmed")

    def test_hook_success_without_snapshot_is_not_reported_as_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "transaction.json"
            record.write_text(json.dumps({"id": "test", "phase": "complete", "pre_deployment_id": None, "failure": None}))
            with patch("anduinos_offline_updater.install.snapshot_records", return_value={record}):
                result = snapshot_result(set(), {"state": "requires-layout-check"})
            self.assertEqual(result["state"], "unconfirmed")
            self.assertFalse(result["transactions"][0]["pre_snapshot_verified"])

    def test_snapshot_inventory_uses_cli_array_and_checks_real_uuid(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "transaction.json"
            record.write_text(json.dumps({"id": "test", "pre_deployment_id": "snapshot", "failure": None}))
            inventory = json.dumps([{"id": "snapshot", "state": "ready", "snapshot_uuid": "actual-uuid"}]).encode()
            with patch("anduinos_offline_updater.install.snapshot_records", return_value={record}), \
                    patch("anduinos_offline_updater.install.run", side_effect=[inventory, b"  UUID: actual-uuid\n"]):
                self.assertEqual(snapshot_result(set(), {"state": "requires-layout-check"})["state"], "created")
            for failure in (b"{}", b"not json"):
                with patch("anduinos_offline_updater.install.snapshot_records", return_value={record}), \
                        patch("anduinos_offline_updater.install.run", return_value=failure):
                    self.assertEqual(snapshot_result(set(), {"state": "requires-layout-check"})["state"], "unconfirmed")
            with patch("anduinos_offline_updater.install.snapshot_records", return_value={record}), \
                    patch("anduinos_offline_updater.install.run", side_effect=TimeoutError("Snapshot service unavailable")):
                self.assertEqual(snapshot_result(set(), {"state": "requires-layout-check"})["state"], "unconfirmed")

    def test_space_counts_cache_and_installation_on_same_filesystem(self):
        with tempfile.TemporaryDirectory() as directory, patch("os.path.ismount", return_value=False):
            # /tmp may itself be a different filesystem on the test host.
            with patch("os.stat", return_value=type("Stat", (), {"st_dev": 1})()):
                result = space_check(Path(directory), {"packages": [{"content": {"size": 20}}]},
                                     {"space": {"package_expansion_upper_estimate_bytes": 30}})
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["required_bytes"], 50 + 512 * 1024 ** 2)
            with patch("shutil.disk_usage", return_value=type("Usage", (), {"free": 1})()):
                with self.assertRaises(BundleError):
                    space_check(Path(directory), {"packages": []}, {"space": {"package_expansion_upper_estimate_bytes": 30}})

    def test_job_names_and_mutable_directories_are_rejected(self):
        for identifier in ("../outside", "--unit=other", "a" * 31, "A" * 32):
            with self.assertRaises(BundleError): jobs.job_path(identifier)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); path.chmod(0o777)
            with self.assertRaises(BundleError): jobs.trusted_directory(path)
            link = path / "link"; link.symlink_to(path, target_is_directory=True)
            with self.assertRaises(BundleError): jobs.trusted_directory(link)

    @unittest.skipUnless(os.geteuid() == 0, "User identity import runs in the root container/VM acceptance")
    def test_import_opens_file_as_original_user_not_administrator(self):
        import pwd
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); path.chmod(0o755)
            private = path / "private"; private.write_bytes(b"private"); private.chmod(0o600)
            with self.assertRaises(BundleError): jobs.copy_as_user(private, path / "denied", pwd.getpwnam("nobody").pw_uid)
            public = path / "public"; public.write_bytes(b"public"); public.chmod(0o644)
            digest, size = jobs.copy_as_user(public, path / "copy", pwd.getpwnam("nobody").pw_uid)
            self.assertEqual(size, 6); self.assertEqual((path / "copy").read_bytes(), b"public")
            self.assertEqual(len(digest), 64)
            link = path / "link"; link.symlink_to(public)
            with self.assertRaises(BundleError): jobs.copy_as_user(link, path / "linked", 0)
            fifo = path / "fifo"; os.mkfifo(fifo)
            with self.assertRaises(BundleError): jobs.copy_as_user(fifo, path / "pipe", 0)
