"""Installation safety checks; these tests never call APT commit on the host."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

import apt_pkg

from anduinos_offline_updater import cli, jobs
from anduinos_offline_updater.install import check_targets, finish_handoff, snapshot_result, space_check
from anduinos_offline_updater.native import BundleError
from anduinos_offline_updater.planning import replaces_runtime


class ApplyCommandTests(unittest.TestCase):
    def apply(self, helper, answers=(), flags=()):
        with patch.object(cli.os, "geteuid", return_value=0), \
                patch.object(cli, "call_helper", side_effect=helper) as service, \
                patch("builtins.input", side_effect=answers) as prompt, \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = cli.main(["apply", *flags, "/tmp/update.atgz"])
        return code, out.getvalue(), err.getvalue(), service.call_args_list, prompt.call_count

    @staticmethod
    def plan(plan_hash="first", state="update-available", **fields):
        changes = [] if state == "already-installed" else [
            {"name": "base-files", "architecture": "all", "before": "2.0.0", "after": "2.0.6"}]
        return {"state": "prepared", "job_id": "test-job", "plan_sha256": plan_hash,
                "preview": {"state": state, "current_version": "2.0.0", "target_version": "2.0.6",
                            "stages": [{"changes": changes}]},
                **fields}

    def test_foreign_removal_flag_and_refusal_do_not_start_installation(self):
        pending = self.plan()
        pending["preview"]["foreign_removals"] = [
            {"name": "paired-library", "architecture": "arm64", "before": "1", "after": None}]
        code, _, _, commands, prompts = self.apply(
            [pending, {}], answers=["no"], flags=["--allow-foreign-removals"])
        self.assertEqual(code, 1)
        self.assertEqual(prompts, 1)
        self.assertEqual(commands, [call("prepare", "/tmp/update.atgz", "--allow-foreign-removals"),
                                    call("discard", "test-job")])

    def test_refusing_or_closing_input_discards_unstarted_job(self):
        for answer in ("", "no", EOFError):
            with self.subTest(answer=answer):
                code, out, _, commands, prompts = self.apply([self.plan(), {}], answers=[answer])
                self.assertEqual(code, 1)
                self.assertEqual(prompts, 1)
                self.assertIn("AnduinOS 2.0.0 → 2.0.6", out)
                self.assertIn("base-files:all  2.0.0 → 2.0.6", out)
                self.assertEqual(commands, [call("prepare", "/tmp/update.atgz"), call("discard", "test-job")])

    def test_waits_for_result_and_reports_reboot(self):
        code, out, _, commands, prompts = self.apply(
            [self.plan(), {"state": "complete", "reboot_required": True}], answers=["yes"])
        self.assertEqual(code, 0)
        self.assertEqual(prompts, 1)
        self.assertEqual(commands[-1], call("start", "test-job", "first"))
        self.assertEqual(out.count("Installing the update"), 1)
        self.assertIn("Update complete.", out)
        self.assertIn("Restart your computer", out)

    def test_yes_confirms_a_new_plan_after_the_updater_handoff(self):
        handoff = self.plan("fresh", confirmation_required=True)
        code, out, _, commands, prompts = self.apply(
            [self.plan(state="updater-update-required"), {"state": "handoff", "job_id": "test-job"},
             {"state": "complete"}], flags=["-y"], statuses=[handoff])
        self.assertEqual(code, 0)
        self.assertEqual(prompts, 0)
        self.assertIn("Remaining update plan", out)
        self.assertEqual(commands[1:], [call("start", "test-job", "first"), call("start", "test-job", "fresh")])

    def test_declining_after_handoff_preserves_the_pending_plan(self):
        handoff = self.plan("fresh", confirmation_required=True)
        code, out, _, commands, prompts = self.apply(
            [self.plan(state="updater-update-required"), handoff], answers=["y", "n"])
        self.assertEqual(code, 1)
        self.assertEqual(prompts, 2)
        self.assertIn("status test-job", out)
        self.assertEqual(commands[1:], [call("start", "test-job", "first")])

    def test_yes_does_not_override_verification_failure(self):
        code, _, err, commands, prompts = self.apply([BundleError("Bad signature")], flags=["--yes"])
        self.assertEqual(code, 2)
        self.assertEqual(prompts, 0)
        self.assertIn("Bad signature", err)
        self.assertEqual(commands, [call("prepare", "/tmp/update.atgz")])

    def test_worker_failure_reports_the_saved_job(self):
        code, out, err, _, _ = self.apply(
            [self.plan(), {"state": "partial", "error": "Package installation failed"}], flags=["-y"])
        self.assertEqual(code, 2)
        self.assertNotIn("Update complete.", out)
        self.assertIn("Package installation failed", err)
        self.assertIn("status test-job", err)

    def test_interrupted_foreground_install_exits_without_reporting_success(self):
        code, _, err, commands, _ = self.apply(
            [self.plan(), KeyboardInterrupt], flags=["-y"])
        self.assertEqual(code, 130)
        self.assertIn("status test-job", err)
        self.assertEqual(commands[-1], call("start", "test-job", "first"))
        self.assertEqual(len(commands), 2)

    def test_already_applied_update_does_not_start_an_installation(self):
        code, out, _, commands, prompts = self.apply([self.plan(state="already-installed"), {}])
        self.assertEqual(code, 0)
        self.assertEqual(prompts, 0)
        self.assertIn("You already have this update.", out)
        self.assertEqual(commands, [call("prepare", "/tmp/update.atgz"), call("discard", "test-job")])


class InstallationChecks(unittest.TestCase):
    def test_interrupted_import_removes_incomplete_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs.atomic_json(root / ".boot-id", jobs.boot_id())
            with patch.object(jobs, "ROOT", root), patch.object(jobs, "trusted_directory", return_value=root), \
                    patch.object(jobs, "copy_as_user", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    jobs.prepare(Path("/tmp/update.atgz"), 0)
            self.assertEqual([entry.name for entry in root.iterdir()], [".boot-id"])

    def test_targets_are_checked_even_if_os_version_already_matches(self):
        apt_pkg.init_config(); apt_pkg.init_system()
        manifest = {"architecture": "amd64", "version_package": "base-files", "allowed_removals": [], "required_installed_targets": [
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

    def test_result_versions_survive_handoff_and_legacy_result_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            jobs.update(path, state="complete", owner_uid=1000)
            jobs.save_plan(path, {"current_version": "2.0.0", "target_version": "2.0.5"})
            with patch("anduinos_offline_updater.jobs.job_path", return_value=path):
                report = jobs.status("unused", 1000)
                self.assertEqual((report["source_version"], report["target_version"]), ("2.0.0", "2.0.5"))
                self.assertNotIn("preview", report)
                with self.assertRaises(BundleError): jobs.status("unused", 1001)
                jobs.update(path, source_version="2.0.0", target_version="2.0.5")
                jobs.save_plan(path, {"current_version": "2.0.5", "target_version": "2.0.5"})
                report = jobs.status("unused", 1000)
                self.assertEqual(report["source_version"], "2.0.0")
                self.assertEqual(report["target_version"], "2.0.5")

    def test_previous_boot_is_forgotten_on_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / ("a" * 32)
            job.mkdir()
            (job / "bundle.atgz").write_bytes(b"part of an import")
            original = root / "my-update.atgz"
            original.write_bytes(b"original")
            jobs.atomic_json(root / ".boot-id", "previous-boot")
            jobs.clean_previous_boot(root, jobs.JOB_NAME, os.getuid())
            self.assertFalse(job.exists())
            self.assertEqual(original.read_bytes(), b"original")
            job.mkdir()
            jobs.clean_previous_boot(root, jobs.JOB_NAME, os.getuid())
            self.assertTrue(job.is_dir(), "Opening a second window must preserve current work")

    def test_first_upgrade_preserves_current_jobs_and_clears_old_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "offline-updater-abcdefgh"
            recent = root / "offline-updater-bcdefghi"
            old.mkdir(); recent.mkdir()
            os.utime(old, (1, 1))
            jobs.clean_previous_boot(root, jobs.PREVIEW_NAME, os.getuid())
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

    def test_previous_boot_plan_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            jobs.update(path, state="prepared", owner_uid=0, boot_id="previous-boot")
            with patch.object(jobs, "job_path", return_value=path):
                with self.assertRaisesRegex(BundleError, "previous boot"):
                    jobs.status("unused", 0)

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
