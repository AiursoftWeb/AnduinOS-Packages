"""Installation safety checks; these tests never call APT commit on the host."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import apt_pkg

from anduinos_offline_updater import jobs
from anduinos_offline_updater.install import check_targets, finish_handoff, snapshot_result, space_check
from anduinos_offline_updater.native import BundleError
from anduinos_offline_updater.planning import replaces_runtime


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

    def test_bootstrap_requires_an_explicit_minimal_installation(self):
        for report in ({"state": "blocked", "blockers": ["Held package"]},
                       {"state": "update-available", "stages": [{"changes": [{"name": "anduinos-offline-updater"}]}]}):
            with self.assertRaises(BundleError): jobs.ensure_installable(report)
        jobs.ensure_installable({"state": "already-installed", "stages": [{"changes": []}]})

    def test_bootstrap_never_accepts_removals_or_dist_upgrade(self):
        report = {"state": "updater-update-required", "stages": [{"stage": {"operation": "install"},
                  "changes": [{"name": "anduinos-offline-updater", "action": "install"}]}]}
        jobs.ensure_installable(report)
        report["stages"][0]["changes"].append({"name": "other", "action": "remove"})
        with self.assertRaises(BundleError): jobs.ensure_installable(report)
        report["stages"][0]["changes"].pop()
        report["stages"][0]["stage"]["operation"] = "dist-upgrade"
        with self.assertRaises(BundleError): jobs.ensure_installable(report)

    def test_handoff_reverifies_and_requires_a_fresh_confirmation(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path / "bundle.atgz").write_bytes(b"immutable")
            job = {"archive_sha256": hashlib.sha256(b"immutable").hexdigest()}
            plan = {"state": "update-available", "stages": [{"changes": [{"name": "base-files"}]}]}
            jobs.update(path, state="handoff", plan_sha256="old", bootstrap_completed=True)
            with patch("anduinos_offline_updater.jobs.inspect_import", return_value=plan):
                self.assertEqual(finish_handoff(path, job), 0)
            status = json.loads((path / "status.json").read_text())
            self.assertEqual(status["state"], "prepared")
            self.assertNotEqual(status["plan_sha256"], "old")
            self.assertTrue(status["confirmation_required"])
            self.assertEqual(json.loads((path / "plan.json").read_text()), plan)
            (path / "bundle.atgz").write_bytes(b"changed")
            with patch("anduinos_offline_updater.jobs.inspect_import") as inspect:
                self.assertEqual(finish_handoff(path, job), 2)
                inspect.assert_not_called()
            self.assertEqual(json.loads((path / "status.json").read_text())["state"], "partial")

    def test_unsupported_installer_after_bootstrap_is_partial_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for report in ({"state": "blocked", "blockers": ["Unknown installer feature"]},
                           {"state": "updater-update-required", "stages": [{"stage": {"operation": "install"},
                            "changes": [{"name": "anduinos-offline-updater", "action": "install"}]}]}):
                with patch("anduinos_offline_updater.install.check_archive"), \
                        patch("anduinos_offline_updater.jobs.inspect_import", return_value=report):
                    self.assertEqual(finish_handoff(path, {}), 2)
                self.assertEqual(json.loads((path / "status.json").read_text())["state"], "partial")

    def test_handoff_covers_native_runtime_without_selecting_all_python_apps(self):
        for name in ("anduinos-offline-updater", "python3", "python3-minimal", "python3-apt",
                     "python3.14", "python3.14-minimal", "libpython3-stdlib", "libpython3.14t64",
                     "libpython3.14-stdlib", "libapt-pkg7.0"):
            self.assertTrue(replaces_runtime(name), name)
        for name in ("python3-requests", "python3-gi", "python3-pyqt6", "vim", "docker-ce"):
            self.assertFalse(replaces_runtime(name), name)

    def test_existing_prepared_jobs_remain_readable_without_a_public_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            jobs.update(path, state="prepared", owner_uid=1000)
            with patch("anduinos_offline_updater.jobs.job_path", return_value=path):
                self.assertEqual(jobs.status("unused", 1000)["state"], "prepared")
                with self.assertRaises(BundleError): jobs.status("unused", 1001)
                plan = {"state": "update-available", "stages": []}
                jobs.save_plan(path, plan)
                self.assertEqual(jobs.status("unused", 1000)["preview"], plan)
                jobs.update(path, confirmation_required=True)
                self.assertEqual(jobs.status("unused", 1000)["preview"], plan)

    def test_no_snapshot_is_not_an_installation_blocker(self):
        with patch("anduinos_offline_updater.install.snapshot_records", return_value=set()):
            self.assertEqual(snapshot_result(set(), {"state": "unavailable"})["state"], "unavailable")
            self.assertEqual(snapshot_result(set(), {"state": "requires-layout-check"})["state"], "unconfirmed")

    def test_completed_job_does_not_repeat_a_satisfied_reboot_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            jobs.update(path, state="complete", owner_uid=1000, reboot_required=True, boot_id="previous-boot")
            with patch("anduinos_offline_updater.jobs.job_path", return_value=path):
                with patch.object(Path, "exists", return_value=False):
                    self.assertFalse(jobs.status("unused", 1000)["reboot_required"])
                with patch.object(Path, "exists", return_value=True):
                    self.assertTrue(jobs.status("unused", 1000)["reboot_required"])
                jobs.update(path, boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
                with patch.object(Path, "exists", return_value=False):
                    self.assertTrue(jobs.status("unused", 1000)["reboot_required"])

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
