"""Build the complete scan result twice, without inspecting or changing the host."""

import unittest
from unittest.mock import patch

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from anduinos_driver_center import app
from anduinos_driver_center.core import (
    AudioState, GraphicsScan, PackageState, PrintingState, XboxState, XboxStatus,
)
from anduinos_secureboot.model import DkmsState, SecureBootState


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

    def test_initial_scan_and_rescan_build_all_pages_and_preserve_selection(self):
        scan = (
            GraphicsScan(),
            SecureBootState(False, True, True, True, "serial", boot_loader="shim",
                            setup_mode=False),
            XboxState(XboxStatus.NOT_INSTALLED, False, False, False, None, False),
            DkmsState(),
            AudioState(PackageState("firmware-sof-anduinos", True, "1"),
                       PackageState("alsa-ucm-conf-anduinos", True, "1"),
                       True, True, (), ()),
            PrintingState(True, True, (), (), None, (), (), (), ()),
        )
        # Keep the real page builders, but isolate all background discovery.
        with patch.object(app.DriverCenterWindow, "refresh"), \
             patch.object(app.FirmwareManager, "start") as firmware_start, \
             patch.object(app.ComputerPage, "reload") as computer_reload, \
             patch.object(app, "scan_system") as scan_system, \
             patch.object(app, "_", side_effect=lambda text: text):
            window = app.DriverCenterWindow(self.application)
            try:
                for selected in ("home", "secure-boot"):
                    with self.subTest(selected=selected):
                        window._selected_page_name = selected
                        window._show_loading()
                        self.assertEqual(window.stack.get_visible_child_name(), "loading")
                        self.assertEqual(window._apply_scan(*scan), GLib.SOURCE_REMOVE)
                        self.assertEqual(window.stack.get_visible_child_name(), selected)
                        expected = ("home", "audio", "printing", "xbox", "secure-boot",
                                    "firmware", "computer")
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
            finally:
                window.destroy()
            firmware_start.assert_called_once()
            computer_reload.assert_called()
            scan_system.assert_not_called()
