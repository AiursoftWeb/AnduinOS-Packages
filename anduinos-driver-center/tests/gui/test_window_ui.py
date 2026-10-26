"""Build the complete scan result twice, without inspecting or changing the host."""

import unittest
from unittest.mock import Mock, patch

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from anduinos_driver_center import app
from anduinos_driver_center.recovery import RecoveryState
from anduinos_driver_center.core import (
    AudioState, GraphicsScan, PackageState, PrintingState, XboxState, XboxStatus,
)
from anduinos_secureboot.model import DkmsState, GrubState, SecureBootState
from anduinos_secureboot.identity import IdentityStatus


class WindowScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()
        cls.application = Adw.Application(
            application_id="com.anduinos.DriverCenter.WindowTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        cls.application.register(None)

    def test_recovery_page_distinguishes_missing_update_and_unknown_states(self):
        from dataclasses import replace
        missing = RecoveryState(False, (), PackageState("anduinos-recovery-env", False, None),
                                None, False, False)
        cases = (
            (missing, ["Recovery partition not found", "Not installed", "Recovery menu entry not found"]),
            (replace(missing, partition=None, grub_entry=None,
                     package=PackageState("anduinos-recovery-env", True, "1"),
                     candidate="2", up_to_date=False),
             ["Unknown", "Update available · 1 → 2", "Unable to read the GRUB menu"]),
            (replace(missing, package=PackageState("anduinos-recovery-env", True, "1"),
                     up_to_date=None),
             ["Recovery partition not found", "Unknown · 1", "Recovery menu entry not found"]),
        )
        for state, expected in cases:
            with self.subTest(expected=expected), patch.object(app, "_", side_effect=lambda text: text):
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                window = Mock()
                window._page_shell.return_value = (Gtk.ScrolledWindow(), content)
                app.DriverCenterWindow._recovery_page(window, state)
                rows = []

                def visit(widget):
                    if isinstance(widget, Adw.ActionRow):
                        rows.append(widget)
                    child = widget.get_first_child()
                    while child:
                        visit(child)
                        child = child.get_next_sibling()

                visit(content)
                self.assertEqual([row.get_subtitle() for row in rows], expected)

    def test_missing_or_small_swap_disables_preparation_and_explains_the_blocker(self):
        messages = {
            "no-swap": "no ordinary swap partition",
            "small-swap": "smaller than 512 MiB",
        }
        for reason, message in messages.items():
            with self.subTest(reason=reason), patch.object(app, "_", side_effect=lambda text: text):
                state = RecoveryState(False, (), PackageState("anduinos-recovery-env", False, None),
                                      None, False, False, {"reason": reason})
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                app.DriverCenterWindow._append_recovery_repair(Mock(), content, state)
                labels, buttons = [], []

                def visit(widget):
                    if isinstance(widget, Gtk.Label): labels.append(widget.get_label())
                    if isinstance(widget, Gtk.Button): buttons.append(widget)
                    child = widget.get_first_child()
                    while child:
                        visit(child)
                        child = child.get_next_sibling()

                visit(content)
                self.assertIn(message, "\n".join(labels))
                self.assertEqual(len(buttons), 1)
                self.assertFalse(buttons[0].get_sensitive())
                self.assertEqual(buttons[0].get_label(), "Shrink swap partition and create recovery partition")

    def test_eligible_preparation_opens_confirmation_instead_of_running_a_command(self):
        with patch.object(app, "_", side_effect=lambda text: text):
            window = Mock()
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            state = RecoveryState(False, (), PackageState("anduinos-recovery-env", False, None),
                                  None, False, False, {"reason": "ready", "plan": {"disk": "/dev/vda"}})
            app.DriverCenterWindow._append_recovery_repair(window, content, state)
            button = content.get_first_child().get_last_child()
            self.assertTrue(button.get_sensitive())
            button.emit("clicked")
            window._confirm_recovery_repair.assert_called_once_with(button, "prepare", state)
            window._run_action.assert_not_called()

    def test_initial_scan_and_rescan_build_all_pages_and_preserve_selection(self):
        scan = (
            GraphicsScan(),
            SecureBootState(False, True, True, True, "serial", boot_loader="shim",
                            setup_mode=False,
                            signing_identity=IdentityStatus(purpose="unified", enrollment="enrolled",
                                key_present=True, key_valid=True, defaults_ready=True),
                            grub=GrubState(installed=True, signature="valid", enrollment="enrolled", deployment="ready", active_chain="trusted")),
            XboxState(XboxStatus.NOT_INSTALLED, False, False, False, None, False),
            DkmsState(),
            AudioState(PackageState("firmware-sof-anduinos", True, "1"),
                       PackageState("alsa-ucm-conf-anduinos", True, "1"),
                       True, True, (), ()),
            PrintingState(True, True, (), (), None, (), (), (), ()),
            RecoveryState(True, ("/dev/vda4",),
                          PackageState("anduinos-recovery-env", True, "2.0.5-6+resolute"),
                          "2.0.5-6+resolute", True, True),
        )
        # Keep the real page builders, but isolate all background discovery.
        with patch.object(app.DriverCenterWindow, "refresh"), \
             patch.object(app.FirmwareManager, "start") as firmware_start, \
             patch.object(app.ComputerPage, "reload") as computer_reload, \
             patch.object(app, "scan_system") as scan_system, \
             patch.object(app, "_", side_effect=lambda text: text):
            window = app.DriverCenterWindow(self.application)
            try:
                for selected in ("home", "secure-boot", "recovery"):
                    with self.subTest(selected=selected):
                        window._selected_page_name = selected
                        window._show_loading()
                        self.assertEqual(window.stack.get_visible_child_name(), "loading")
                        self.assertEqual(window._apply_scan(*scan), GLib.SOURCE_REMOVE)
                        self.assertEqual(window.stack.get_visible_child_name(), selected)
                        expected = ("home", "audio", "printing", "xbox", "secure-boot",
                                    "recovery", "firmware", "computer")
                        self.assertEqual(window.stack.get_pages().get_n_items(), len(expected))
                        for index, page_name in enumerate(expected):
                            self.assertIsNotNone(window.stack.get_child_by_name(page_name))
                            self.assertEqual(window.device_list.get_row_at_index(index).page_name,
                                             page_name)
                        self.assertIsNone(window.device_list.get_row_at_index(len(expected)))
                        secure_row = window.device_list.get_row_at_index(4)
                        self.assertEqual(secure_row.subtitle_label.get_label(), "Disabled")
                        self.assertTrue(window.refresh_button.get_sensitive())
                        self.assertFalse(window._rebuilding_navigation)
                        page = window.stack.get_child_by_name("recovery")
                        rows = []

                        def visit(widget):
                            if isinstance(widget, Adw.ActionRow):
                                rows.append(widget)
                            child = widget.get_first_child()
                            while child:
                                visit(child)
                                child = child.get_next_sibling()

                        visit(page)
                        self.assertEqual([row.get_title() for row in rows],
                                         ["Recovery partition", "Recovery package", "GRUB menu entry"])
                        self.assertIn("Up to date", rows[1].get_subtitle())
                        self.assertEqual(rows[2].get_subtitle(), "Present")
            finally:
                window.destroy()
            firmware_start.assert_called_once()
            computer_reload.assert_called()
            scan_system.assert_not_called()
