"""Smoke tests for the in-process Software Source window."""

import gettext
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class SoftwareSourceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("CONTROL_PANEL_UI_TESTS") != "1":
            raise RuntimeError("Run the gui test profile")
        from anduinos_control_panel import app, software_sources

        cls.app = app
        cls.ui = software_sources
        app.Adw.init()

    def setUp(self):
        self.owner = self.ui.Adw.Window()
        with patch.object(
            self.ui,
            "current_mirror",
            return_value="https://archive.ubuntu.com/ubuntu/",
        ):
            self.window = self.ui.SoftwareSourceWindow(self.owner)

    def tearDown(self):
        self.window.destroy()
        self.owner.destroy()

    def test_window_keeps_migrated_actions_inside_control_panel(self):
        self.assertEqual(self.window.get_title(), self.ui._("Software Source"))
        self.assertEqual(
            self.window.mirror_button.get_label().strip(),
            self.ui._("  Switch to Fastest Mirror  ").strip(),
        )
        self.assertEqual(
            self.window.update_button.get_label().strip(),
            self.ui._("  Check for Updates  ").strip(),
        )
        self.assertIn(
            "https://archive.ubuntu.com/ubuntu/",
            self.window.current_source.get_label(),
        )
        self.assertFalse(
            self.window.mirror_button.has_css_class("suggested-action")
        )
        self.assertFalse(
            self.window.update_button.has_css_class("suggested-action")
        )

    def test_update_state_is_visible_and_busy_window_cannot_be_closed(self):
        self.window._set_busy(True)
        self.assertFalse(self.window.get_deletable())
        self.assertFalse(self.window.mirror_button.get_sensitive())
        self.assertFalse(self.window.apply_button.get_sensitive())
        self.window._set_busy(False)
        self.assertTrue(self.window.get_deletable())

        self.window._checked(2)
        self.assertTrue(self.window._updates_available)
        self.assertEqual(
            self.window.update_button.get_label().strip(),
            self.ui._("  Install Updates  ").strip(),
        )
        self.assertEqual(self.window.status.get_label(), self.ui._("Updates are available."))

    def test_advanced_selection_and_custom_url_share_confirmed_switch(self):
        self.assertIsNotNone(self.window.pages.get_child_by_name("advanced"))
        self.assertEqual(
            self.window.mirror_dropdown.get_model().get_string(0),
            self.ui._("Custom address…"),
        )
        self.assertEqual(self.window.mirror_dropdown.get_selected(), 1)
        self.assertEqual(self.window._selected_mirror(), self.ui.MIRRORS[0])
        selected = self.ui.MIRRORS[1]
        self.window.mirror_dropdown.set_selected(2)
        self.assertEqual(self.window._selected_mirror(), selected)
        with (
            patch.object(self.ui.Adw, "MessageDialog") as dialog_type,
            patch.object(self.ui.threading, "Thread") as thread_type,
        ):
            self.window._apply_selected_mirror(None)
            self.assertIn(selected, dialog_type.call_args.kwargs["body"])
            response = dialog_type.return_value.connect.call_args.args[1]
            response(dialog_type.return_value, "switch")
            thread_type.return_value.start.assert_called_once()
        self.window._set_busy(False)
        self.window.mirror_dropdown.set_selected(0)
        self.assertTrue(self.window.custom_entry.get_visible())
        self.window.custom_entry.set_text("file:///tmp/apt")
        self.window._apply_selected_mirror(None)
        self.assertEqual(
            self.window.status.get_label(),
            self.ui._("Enter a valid HTTP or HTTPS mirror address."),
        )

    def test_custom_mirror_speed_button_tests_entered_address(self):
        uri = "https://example.org/ubuntu/"
        self.window.mirror_dropdown.set_selected(0)
        self.window.custom_entry.set_text(uri)
        measurement = self.ui.MirrorMeasurement(uri, 14.0, 90.0)
        with (
            patch.object(self.ui, "probe_mirror") as probe,
            patch.object(self.ui, "measure_mirrors", return_value=(measurement,)) as measure,
            patch.object(self.ui.threading, "Thread") as thread_type,
            patch.object(self.ui.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)),
        ):
            self.window._test_mirrors(None)
            thread_type.call_args.kwargs["target"]()
        probe.assert_called_once()
        self.assertEqual(measure.call_args.kwargs["candidates"], (uri,))
        self.assertIn(uri, self.window.status.get_label())

    def test_speed_button_tests_only_selected_current_custom_mirror(self):
        uri = "https://mirror.aiursoft.com/ubuntu/"
        measurement = self.ui.MirrorMeasurement(uri, 12.0, 80.0)
        with patch.object(self.ui, "current_mirror", return_value=uri):
            window = self.ui.SoftwareSourceWindow(self.owner)
            try:
                self.assertEqual(window.mirror_dropdown.get_selected(), 0)
                self.assertEqual(window.custom_entry.get_text(), uri)
                self.assertTrue(window.custom_entry.get_visible())
                with (
                    patch.object(self.ui, "probe_mirror") as probe,
                    patch.object(self.ui, "measure_mirrors", return_value=(measurement,)) as measure,
                    patch.object(self.ui.threading, "Thread") as thread_type,
                    patch.object(self.ui.GLib, "idle_add", side_effect=lambda callback, *args: callback(*args)),
                ):
                    window._test_mirrors(None)
                    thread_type.call_args.kwargs["target"]()
                probe.assert_called_once()
                self.assertEqual(measure.call_args.kwargs["candidates"], (uri,))
                output = window.output.get_buffer()
                text = output.get_text(output.get_start_iter(), output.get_end_iter(), False)
                self.assertNotIn(self.ui.MIRRORS[1], text)
                self.assertIn(uri, window.status.get_label())
            finally:
                window.destroy()

    def test_control_panel_reuses_one_internal_window(self):
        owner = type("Owner", (), {})()
        owner._software_source_window = None
        with patch.object(self.app, "SoftwareSourceWindow") as window_type:
            self.app.ControlPanelWindow._open_software_source(owner)
            self.app.ControlPanelWindow._open_software_source(owner)
        window_type.assert_called_once_with(owner)
        self.assertEqual(window_type.return_value.present.call_count, 2)

    def test_mirror_switch_labels_use_each_compiled_catalog(self):
        root = Path(__file__).resolve().parents[2]
        original = "https://archive.ubuntu.com/ubuntu/"
        selected = "https://mirrors.xtom.nl/ubuntu/"
        measurement = self.ui.MirrorMeasurement(selected, 12.0, 80.0)
        with tempfile.TemporaryDirectory() as directory:
            for catalog in sorted((root / "po").glob("*.po")):
                compiled = Path(directory) / f"{catalog.stem}.mo"
                subprocess.run(
                    ["msgfmt", "--check", "-o", str(compiled), str(catalog)],
                    check=True, capture_output=True,
                )
                with compiled.open("rb") as stream:
                    translate = gettext.GNUTranslations(stream).gettext
                with (
                    self.subTest(locale=catalog.stem),
                    patch.object(self.ui, "_", translate),
                    patch.object(
                        self.ui, "current_mirror", return_value=original
                    ) as current,
                    patch.object(self.ui.Adw, "MessageDialog") as dialog_type,
                    patch.object(self.ui.threading, "Thread") as thread_type,
                    patch.object(
                        self.ui.GLib, "idle_add",
                        side_effect=lambda callback, *args: callback(*args),
                    ),
                ):
                    window = self.ui.SoftwareSourceWindow(self.owner)
                    try:
                        self.assertEqual(
                            window.current_source.get_label(),
                            original,
                        )
                        self.assertEqual(
                            window.current_source_title.get_label(),
                            translate("Current mirror"),
                        )
                        self.assertEqual(
                            window._failure_message(1, "apt-get update exited with status 100"),
                            translate("Review Terminal Output for details."),
                        )
                        self.assertEqual(
                            window._failure_message(1, "the original source was restored"),
                            translate("The original source was restored."),
                        )
                        window._set_busy(True)
                        window._confirm_mirror(measurement)
                        dialog = dialog_type.return_value
                        signal, response = dialog.connect.call_args.args
                        self.assertEqual(signal, "response")
                        response(dialog, "switch")
                        self.assertEqual(
                            window.status.get_label(), translate("Switching mirror…")
                        )
                        self.assertFalse(window.mirror_button.get_sensitive())
                        thread_type.return_value.start.assert_called_once()
                        current.return_value = selected
                        with patch.object(
                            window, "_run_helper", return_value=(0, "")
                        ) as helper:
                            window._switch_worker(selected)
                        helper.assert_called_once_with("switch-mirror", selected)
                        self.assertEqual(
                            window.status.get_label(),
                            translate("✓ Mirror switched and system updated."),
                        )
                        self.assertEqual(
                            window.current_source.get_label(),
                            selected,
                        )
                        self.assertEqual(window._selected_mirror(), selected)
                        self.assertEqual(
                            window.mirror_dropdown.get_selected(),
                            window._mirror_options.index(selected) + 1,
                        )
                        self.assertTrue(window.mirror_button.get_sensitive())
                    finally:
                        window.destroy()

    def test_custom_mirror_switch_updates_advanced_selection(self):
        selected = "https://example.org/ubuntu/"
        with (
            patch.object(self.ui, "current_mirror", return_value=selected),
            patch.object(self.window, "_run_helper", return_value=(0, "")),
            patch.object(
                self.ui.GLib, "idle_add",
                side_effect=lambda callback, *args: callback(*args),
            ),
        ):
            self.window._switch_worker(selected)
        self.assertEqual(self.window.mirror_dropdown.get_selected(), 0)
        self.assertEqual(self.window.custom_entry.get_text(), selected)
        self.assertEqual(self.window._selected_mirror(), selected)

    def test_mirror_failure_status_is_translated_and_raw_details_stay_in_output(self):
        with (
            patch.object(self.ui, "measure_mirrors", side_effect=RuntimeError("No Ubuntu archive mirror is reachable")),
            patch.object(self.ui.threading, "Thread") as thread_type,
            patch.object(
                self.ui.GLib, "idle_add",
                side_effect=lambda callback, *args: callback(*args),
            ),
        ):
            self.window._find_mirror(None)
            thread_type.call_args.kwargs["target"]()
        self.assertEqual(
            self.window.status.get_label(),
            self.ui._("✗ Mirror test failed: ")
            + self.ui._("No Ubuntu archive mirror is reachable."),
        )
        output = self.window.output.get_buffer()
        self.assertIn(
            "No Ubuntu archive mirror is reachable",
            output.get_text(output.get_start_iter(), output.get_end_iter(), False),
        )

    def test_helper_errors_have_localized_status_and_keep_rollback_result(self):
        self.assertEqual(
            self.window._failure_message(1, "Software source operation failed: Selected mirror failed; the original source was restored"),
            self.ui._("The original source was restored."),
        )
        self.assertEqual(
            self.window._failure_message(1, "Software source operation failed: Selected mirror failed; the original source was restored, but refreshing it also failed"),
            self.ui._("The original source was restored, but refreshing it failed."),
        )
        self.assertEqual(
            self.window._failure_message(1, "apt-get update exited with status 100"),
            self.ui._("Review Terminal Output for details."),
        )


if __name__ == "__main__":
    unittest.main()
