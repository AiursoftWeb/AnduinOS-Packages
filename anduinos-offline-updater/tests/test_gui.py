"""Native GTK interactions with real signed fixtures and the CLI inspector."""
import hashlib
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.environ.get("DISPLAY"), "Run with the APKG gui profile")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from anduinos_offline_updater import gui
        from test_preview import PreviewTests
        cls.gui = gui
        cls.fixtures = PreviewTests
        cls.fixtures.setUpClass()
        cls.application = gui.Application()
        cls.application.register(None)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.tearDownClass()
        cls.application.quit()

    def setUp(self):
        self.fixture = self.fixtures()
        self.fixture.setUp()
        self.state_patch = patch.object(self.gui.GLib, "get_user_state_dir", return_value=str(self.fixture.work / "state"))
        self.state_patch.start()
        self.window = self.gui.UpdateWindow(self.application)
        self.window.present()
        self.status = Path("/var/lib/dpkg/status")
        self.before = hashlib.sha256(self.status.read_bytes()).hexdigest()

    def tearDown(self):
        self.window.close()
        self.state_patch.stop()
        self.fixture.tearDown()
        self.assertEqual(hashlib.sha256(self.status.read_bytes()).hexdigest(), self.before)

    def file(self, path):
        return self.gui.Gio.File.new_for_path(str(path))

    def choose_dialog(self):
        widget = self.window.content.get_first_child()
        while widget is not None:
            if isinstance(widget, self.gui.Gtk.Button) and widget.get_label() == self.gui._("Choose Update File"):
                widget.emit("clicked")
                break
            widget = widget.get_next_sibling()
        else:
            self.fail("Choose Update File button is missing")
        deadline = time.monotonic() + 5
        context = self.gui.GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            for dialog in self.gui.Gtk.Window.get_toplevels():
                if isinstance(dialog, self.gui.Gtk.FileChooserDialog) and dialog.get_visible():
                    return dialog
            time.sleep(0.01)
        self.fail("Clicking Choose Update File did not open the file chooser")

    def test_file_chooser_can_be_cancelled_and_opened_again(self):
        for _attempt in range(2):
            dialog = self.choose_dialog()
            dialog.response(self.gui.Gtk.ResponseType.CANCEL)
            context = self.gui.GLib.MainContext.default()
            while context.pending():
                context.iteration(False)
            self.assertFalse(self.window.busy)
            self.assertIsNone(self.window.report)
            self.assertFalse(self.window.back.get_visible())

    def complete(self, path, *, close=False, use_picker=False):
        original = self.gui.Gio.Subprocess.new
        commands = []
        def spawn(argv, flags):
            commands.append(argv[:])
            # Trust only this fixture in the child. The product UI has no key
            # picker, root selector, authentication or test mode.
            return original([*argv, "--keyring", str(self.fixture.keyring),
                             "--system-root", str(self.fixture.system)], flags)
        with patch.object(self.gui.Gio.Subprocess, "new", side_effect=spawn):
            if use_picker:
                dialog = self.choose_dialog()
                self.assertTrue(dialog.set_file(self.file(path)))
                deadline = time.monotonic() + 5
                context = self.gui.GLib.MainContext.default()
                while (dialog.get_file() is None or dialog.get_file().get_path() != str(path)) and time.monotonic() < deadline:
                    while context.pending():
                        context.iteration(False)
                    time.sleep(0.01)
                self.assertEqual(dialog.get_file().get_path(), str(path))
                dialog.response(self.gui.Gtk.ResponseType.ACCEPT)
                while not commands and time.monotonic() < deadline:
                    while context.pending():
                        context.iteration(False)
                    time.sleep(0.01)
                self.assertTrue(commands, "Selected file was not sent to the inspector")
            else:
                self.assertTrue(self.window.inspect_files([self.file(path)]))
        if close:
            self.window.close()
        deadline = time.monotonic() + 30
        context = self.gui.GLib.MainContext.default()
        while self.window.busy and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)
        self.assertFalse(self.window.busy, "Inspection did not finish")
        self.assertEqual(commands[0][3], "inspect")
        self.assertNotIn("pkexec", commands[0])
        self.assertNotIn(self.gui.PRO_PACKAGES_URL, commands[0])
        return self.window.report

    def test_home_links_to_benefits_without_opening_it(self):
        self.assertEqual(self.window.package_link.get_uri(),
                         "https://www.anduinos.com/pro/offline-update-package")
        self.assertFalse(self.window.busy)
        self.assertIsNone(self.window.report)

    def test_remote_multiple_directory_and_wrong_extension_rejected(self):
        remote = self.gui.Gio.File.new_for_uri("https://example.invalid/update.atgz")
        for files in ([remote], [], [self.file(self.fixture.bundle)] * 2,
                      [self.file(self.fixture.work)], [self.file(self.fixture.keyring)]):
            with self.subTest(files=files):
                self.assertFalse(self.window.inspect_files(files))
                self.assertFalse(self.window.busy)
                self.assertIsNone(self.window.report)

    def test_valid_drop_runs_real_inspector_without_activation(self):
        report = self.complete(self.fixture.bundle)
        self.assertEqual(report["state"], "update-available", report)
        self.assertEqual(report["current_version"], "2.0.0")
        self.assertEqual(report["target_version"], "2.0.5")
        self.assertFalse(report["can_install"])
        self.assertTrue(report["read_only"])

    def test_file_chooser_selection_runs_real_inspector(self):
        report = self.complete(self.fixture.bundle, use_picker=True)
        self.assertEqual(report["state"], "update-available", report)
        self.assertEqual(report["target_version"], "2.0.5")
        self.assertFalse(report["can_install"])

    def test_failed_inspector_shows_the_readable_error(self):
        process = Mock()
        process.communicate_utf8_finish.return_value = (True, "", "")
        process.get_if_exited.return_value = True
        process.get_exit_status.return_value = 1
        application = Mock()
        self.window.inspected(process, None, (application, self.fixture.bundle))
        self.assertIn(self.gui._("The update could not be checked. Try choosing the file again."), self.labels())
        application.release.assert_called_once()

    def test_already_installed_uses_actual_required_targets(self):
        self.fixture.state("2.0.5", "5")
        report = self.complete(self.fixture.bundle)
        self.assertEqual(report["state"], "already-installed", report)
        self.assertTrue(all(not s["changes"] for s in report["stages"]))

    def test_invalid_archive_is_not_displayed_as_an_update(self):
        file = self.fixture.work / "<fake>.atgz"
        file.write_bytes(b"not an archive")
        self.assertIsNone(self.complete(file))
        self.assertTrue(self.window.back.get_visible())
        self.assertIn(self.gui._("This file has not passed verification."), self.labels())
        self.assertNotIn(self.gui._("Verified"), self.labels())
        self.assertNotIn(self.gui._("Update package"), self.labels())

    def test_verified_older_package_shows_both_versions_and_next_steps(self):
        self.fixture.state("2.0.6", "6")
        report = self.complete(self.fixture.bundle)
        self.assertEqual(report["state"], "blocked")
        self.assertEqual(report["verification"], "verified")
        for label in ("2.0.6", "2.0.5", "resolute · amd64", "2026-11-11 00:00",
                      self.gui._("Verified"), self.gui._("Choose another file"), self.gui._("Close")):
            self.assertIn(label, self.labels())
        self.assertIn(self.gui._("Your system is newer than this update. Choose an update for {version} or later.").format(
            version="2.0.6"), self.labels())
        self.assertNotIn(self.gui._("Continue"), self.labels())
        self.assertNotIn(self.gui._("Start Update"), self.labels())
        self.assertFalse(self.window.content.get_last_child().get_expanded())
        with patch.object(self.window, "command") as command:
            self.window.prepare()
            command.assert_not_called()

    def test_wrong_architecture_displays_machine_and_package_without_install(self):
        (self.fixture.system / "var/lib/dpkg/arch").write_text("arm64\n")
        report = self.complete(self.fixture.bundle)
        self.assertEqual(report["state"], "blocked")
        self.assertIn("resolute · arm64", self.labels())
        self.assertIn("resolute · amd64", self.labels())
        self.assertNotIn(self.gui._("Continue"), self.labels())

    def test_different_series_has_one_branch_explanation_not_three_duplicates(self):
        (self.fixture.system / "usr/lib/os-release").write_text(
            "ID=anduinos\nVERSION_ID=2.1.0\nUBUNTU_CODENAME=stonking\n")
        report = self.complete(self.fixture.bundle)
        self.assertEqual(report["state"], "blocked")
        self.assertIn("stonking · amd64", self.labels())
        self.assertIn("resolute · amd64", self.labels())
        self.assertEqual(self.window.incompatibility(report), self.gui._(
            "This package is for a different system branch. Choose an update for {suite}.").format(suite="stonking"))
        self.assertIn("downgrade", report["compatibility_issues"], "Full diagnostics remain available")

    def test_closing_window_allows_child_to_clean_up(self):
        directory = self.fixture.work / "user-cache"
        with patch.object(self.gui.GLib, "get_user_cache_dir", return_value=str(directory)):
            self.complete(self.fixture.bundle, close=True)
        self.assertFalse(any((directory / "anduinos-offline-updater").iterdir()))

    def job_report(self, preview, **fields):
        return dict(job_id="a" * 32, state="prepared", plan_sha256="b" * 64, preview=preview, **fields)

    def labels(self):
        result = []
        def walk(widget):
            if isinstance(widget, self.gui.Gtk.Label): result.append(widget.get_label())
            child = widget.get_first_child()
            while child:
                walk(child)
                child = child.get_next_sibling()
        walk(self.window.content)
        return result

    def test_install_requires_root_import_and_exact_root_plan_confirmation(self):
        preview = self.complete(self.fixture.bundle)
        with patch.object(self.window, "command") as command:
            self.window.prepare()
            self.assertEqual(command.call_args.args[0], ["prepare", str(self.fixture.bundle)])
            prepared = self.job_report(preview)
            self.window.prepared(prepared, None)
            self.assertFalse(self.window.back.get_visible())
            self.assertEqual(json.loads(self.window.bookmark.read_text())["job_id"], "a" * 32)
            self.window.start_update()
            self.assertEqual(command.call_args.args[0], ["install", "a" * 32, "--confirm-plan", "b" * 64])
            self.window.start_update()
            self.assertEqual(command.call_count, 2, "Double click must not start another request")

    def test_foreign_removal_lists_dependencies_and_requires_destructive_confirmation(self):
        self.fixture.foreign_pair(extra=self.fixture.package(
            "foreign-dev", "1", "paired-library (= 1)", architecture="arm64"))
        preview = self.complete(self.fixture.bundle)
        self.assertEqual(preview["state"], "foreign-removal-required", preview)
        self.assertIn("paired-library:arm64", self.labels())
        self.assertIn("foreign-dev:arm64", self.labels())
        with patch.object(self.window, "command") as command:
            button = self.window.content.get_first_child()
            while button is not None:
                if isinstance(button, self.gui.Gtk.Button) and button.get_label() == self.gui._("Cancel"):
                    button.emit("clicked")
                    break
                button = button.get_next_sibling()
            else:
                self.fail("Removal review must offer Cancel")
            self.assertIsNone(self.window.report)
            self.assertIsNone(self.window.job)
            command.assert_not_called()
            self.window.show_report(self.fixture.bundle, preview)
            self.window.prepare()
            self.assertEqual(command.call_args.args[0],
                             ["prepare", str(self.fixture.bundle), "--allow-foreign-removals"])
            approved = dict(preview, state="update-available", blockers=[])
            self.window.prepared(self.job_report(approved), None)
            command.reset_mock()
            self.window.start_update()
            dialog = self.window.get_visible_dialog()
            self.assertIsInstance(dialog, self.gui.Adw.AlertDialog)
            self.assertEqual(dialog.get_default_response(), "cancel")
            package_list = dialog.get_extra_child()
            self.assertEqual(package_list.get_max_content_height(), 240)
            label = package_list.get_child()
            if isinstance(label, self.gui.Gtk.Viewport):
                label = label.get_child()
            self.assertIn("foreign-dev:arm64", label.get_label())
            dialog.emit("response", "cancel")
            dialog.force_close()
            command.assert_not_called()
            self.assertFalse(self.window.busy)
            self.window.start_update()
            dialog = self.window.get_visible_dialog()
            dialog.emit("response", "remove")
            self.assertEqual(command.call_args.args[0],
                             ["install", "a" * 32, "--confirm-plan", "b" * 64])
            dialog.force_close()

    def test_cancelled_import_does_not_create_a_session_or_offer_install(self):
        self.window.busy = True
        self.window.prepared(None, "Authorization cancelled")
        self.assertFalse(self.window.busy)
        self.assertIsNone(self.window.job)
        self.assertFalse(self.window.bookmark.exists())
        self.assertIn(self.gui._("No system packages were changed."), self.labels())

    def test_handoff_requires_a_new_confirmation_and_never_autostarts(self):
        preview = self.complete(self.fixture.bundle)
        self.window.job = "a" * 32
        with patch.object(self.window, "command") as command:
            self.window.show_job(dict(self.job_report(preview, confirmation_required=True), plan_sha256="c" * 64))
            command.assert_not_called()
            self.assertIn(self.gui._("The updater has been upgraded. Review and confirm the remaining update."), self.labels())
            self.window.start_update()
            self.assertEqual(command.call_args.args[0][-1], "c" * 64)

    def test_progress_is_real_and_does_not_rebuild_the_page_or_allow_another_file(self):
        self.window.job = "a" * 32
        report = dict(job_id=self.window.job, state="installing", stage=1, stage_count=2,
                      progress=dict(phase="dpkg", package="vim", percent=35))
        self.window.show_job(report)
        content = self.window.content
        bar = self.window.progress_bar
        self.assertAlmostEqual(bar.get_fraction(), .35)
        report["progress"]["percent"] = 64
        self.window.show_job(report)
        self.assertIs(self.window.content, content)
        self.assertIs(self.window.progress_bar, bar)
        self.assertAlmostEqual(bar.get_fraction(), .64)
        self.assertFalse(self.window.inspect_files([self.file(self.fixture.bundle)]))
        self.assertFalse(self.window.back.get_visible())

    def test_success_snapshot_and_reboot_are_not_assumed(self):
        self.window.job = "a" * 32
        self.window.show_job(dict(state="complete", source_version="2.0.0", target_version="2.0.5",
                                  reboot_required=True, snapshot=dict(state="unconfirmed")))
        self.assertIn(self.gui._("Update complete"), self.labels())
        self.assertIn(self.gui._("AnduinOS {source} → {target}").format(source="2.0.0", target="2.0.5"), self.labels())
        self.assertIn(self.gui._("Done"), self.labels())
        icon = self.window.content.get_first_child().get_icon_name()
        theme = self.gui.Gtk.IconTheme.get_for_display(self.window.get_display())
        self.assertTrue(theme.has_icon(icon), "Success must use an icon available in the standard theme")
        self.assertIn(self.gui._("Restart your computer to finish applying the update."), self.labels())
        details = self.window.content.get_last_child()
        self.assertFalse(details.get_expanded())
        self.assertIn('"state": "unconfirmed"', details.get_child().get_first_child().get_label())
        self.assertNotIn(self.gui._("System snapshot created"), self.labels())
        self.assertFalse(self.window.footer.get_visible())
        self.assertTrue(self.window.content.get_first_child().has_css_class("success"))

    def test_partial_and_interrupted_never_offer_blind_install_retry(self):
        self.window.job = "a" * 32
        for state in ("partial", "interrupted", "failed", "blocked"):
            self.window.show_job(dict(state=state, source_version="2.0.0", target_version="2.0.5",
                                      error="<dpkg needs inspection>"))
            self.assertIn(self.gui._("Update could not start") if state == "blocked" else
                          self.gui._("Update failed"), self.labels())
            self.assertNotIn(self.gui._("Update complete"), self.labels())
            self.assertNotIn(self.gui._("Start Update"), self.labels())
            self.assertNotIn(self.gui._("No system packages were changed."), self.labels())
            self.assertIn(self.gui._("Planned update: {source} → {target}").format(source="2.0.0", target="2.0.5"), self.labels())
            self.assertIn(self.gui._("Check the details before trying again."), self.labels())
            self.assertNotIn(self.gui._("Done"), self.labels())
            details = self.window.content.get_last_child()
            self.assertFalse(details.get_expanded())
            self.assertIn("<dpkg needs inspection>", details.get_child().get_first_child().get_label())
            self.assertNotIn("<dpkg needs inspection>", self.labels())
            self.assertTrue(self.window.content.get_first_child().has_css_class(
                "warning" if state == "blocked" else "error"))

    def test_done_closes_without_authorization_reboot_or_erasing_diagnostics(self):
        self.window.job = "a" * 32
        self.window.remember()
        self.window.show_job(dict(state="complete", source_version="2.0.0", target_version="2.0.5", reboot_required=False))
        self.assertIn(self.gui._("You can keep using your computer."), self.labels())
        self.assertNotIn(self.gui._("Restart your computer to finish applying the update."), self.labels())
        with patch.object(self.window, "command") as command:
            button = self.window.content.get_last_child().get_prev_sibling()
            self.assertEqual(button.get_label(), self.gui._("Done"))
            button.emit("clicked")
            command.assert_not_called()
        self.assertTrue(self.window.closed)
        self.assertTrue(self.window.bookmark.exists())
        self.assertTrue(self.fixture.bundle.exists())

    def test_already_installed_shows_one_version_without_claiming_an_upgrade(self):
        self.window.show_job(dict(state="already-installed", source_version="2.0.5", target_version="2.0.5"))
        self.assertIn(self.gui._("You already have this update"), self.labels())
        self.assertIn(self.gui._("AnduinOS {version}").format(version="2.0.5"), self.labels())
        self.assertIn(self.gui._("Done"), self.labels())

    def test_reopen_reconnects_without_reinstalling_or_trusting_a_saved_hash(self):
        preview = self.complete(self.fixture.bundle)
        self.window.prepared(self.job_report(preview), None)
        self.window.close()
        with patch.object(self.gui.UpdateWindow, "command") as command:
            self.window = self.gui.UpdateWindow(self.application)
            self.window.present()
            self.assertEqual(command.call_args.args[0], ["status", "a" * 32])
        self.window.show_job(dict(state="installing", progress={}))
        with patch.object(self.window, "command") as command:
            self.window.close()
            command.assert_not_called()
            self.assertIsNone(self.window.poll_source)
        self.assertTrue(self.window.bookmark.exists())

    def test_response_loss_keeps_the_job_and_only_offers_reconnect(self):
        self.window.job = "a" * 32
        self.window.started(None, "Caller response was lost")
        self.assertTrue(self.window.busy)
        self.assertIn(self.gui._("Reconnect"), self.labels())
        self.assertNotIn(self.gui._("Start Update"), self.labels())

    def test_session_removed_from_cli_does_not_leave_the_app_stuck(self):
        self.window.job = "a" * 32
        self.window.remember()
        with patch("anduinos_offline_updater.jobs.ROOT", self.fixture.work / "removed-jobs"):
            self.window.queried(None, "Job no longer exists")
        self.assertIsNone(self.window.job)
        self.assertFalse(self.window.bookmark.exists())
        self.assertFalse(self.window.busy)

    def test_discard_is_explicit_and_only_clears_bookmark_after_success(self):
        preview = self.complete(self.fixture.bundle)
        self.window.prepared(self.job_report(preview), None)
        with patch.object(self.window, "command") as command:
            self.window.discard()
            self.assertEqual(command.call_args.args[0], ["discard", "a" * 32])
            self.assertTrue(self.window.bookmark.exists())
            self.window.discarded(dict(state="discarded"), None)
        self.assertIsNone(self.window.job)
        self.assertFalse(self.window.bookmark.exists())
        self.assertTrue(self.fixture.bundle.exists())


if __name__ == "__main__":
    unittest.main()
