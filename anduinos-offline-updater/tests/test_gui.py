"""Native GTK interactions with real signed fixtures and the CLI inspector."""
import hashlib
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
        self.window = self.gui.UpdateWindow(self.application)
        self.window.present()
        self.status = Path("/var/lib/dpkg/status")
        self.before = hashlib.sha256(self.status.read_bytes()).hexdigest()

    def tearDown(self):
        self.window.close()
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
        self.assertEqual(self.window.content.get_last_child().get_label(),
                         self.gui._("The update could not be checked. Try choosing the file again."))
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

    def test_closing_window_allows_child_to_clean_up(self):
        directory = self.fixture.work / "user-cache"
        with patch.object(self.gui.GLib, "get_user_cache_dir", return_value=str(directory)):
            self.complete(self.fixture.bundle, close=True)
        self.assertFalse(any((directory / "anduinos-offline-updater").iterdir()))


if __name__ == "__main__":
    unittest.main()
