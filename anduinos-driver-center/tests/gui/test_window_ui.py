"""Build the complete scan result twice, without inspecting or changing the host."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from anduinos_driver_center import app
from anduinos_driver_center.recovery import RecoveryState
from anduinos_driver_center.core import (
    AudioState, DriverOption, HardwareDevice, GraphicsScan, PackageState, PrintingState, XboxState, XboxStatus,
)
from anduinos_secureboot.model import DkmsState, GrubState, SecureBootState
from anduinos_secureboot.identity import IdentityStatus


class WindowScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()
        try:
            import gi
            gi.require_version("Fwupd", "2.0")
            from gi.repository import Fwupd  # noqa: F401
        except (ValueError, ImportError, RuntimeError):
            cls._fwupd_patcher = patch("anduinos_driver_center.firmware._fwupd_bindings")
            mock = cls._fwupd_patcher.start()
            mock.return_value.Client.new.return_value = Mock()
        cls.application = Adw.Application(
            application_id="com.anduinos.DriverCenter.WindowTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        cls.application.register(None)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_fwupd_patcher"):
            cls._fwupd_patcher.stop()

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

    def test_encryption_page_shows_mixed_coverage_without_offering_ext4_snapshot_repair(self):
        from anduinos_driver_center.encryption import EncryptionState, VolumeState
        from anduinos_driver_center.encryption_ui import create_encryption_page
        missing = RecoveryState(False, (), PackageState("anduinos-recovery-env", False, None),
                                None, False, False)
        encrypted = EncryptionState(
            VolumeState("/dev/mapper/system", "ext4", True),
            VolumeState("/dev/vda3", "ext4", False),
            (VolumeState("/dev/vda2", "swap", False), VolumeState("/dev/zram0", memory=True)), True,
        )
        ready = replace(missing, partition=True, grub_entry=True,
                        package=PackageState("anduinos-recovery-env", True, "1"))
        cases = (
            (encrypted, missing, ["Encrypted · ext4", "Not encrypted · ext4",
                                 "Unencrypted disk swap is active", "Memory-based swap; not a disk partition",
                                 "Not applicable to ext4. Use Rescue Center from the Live USB."], False),
            (replace(encrypted, root=VolumeState("/dev/mapper/system", "btrfs", True)),
             ready, ["Encrypted · btrfs", "Not encrypted · ext4", "Unencrypted disk swap is active",
                     "Memory-based swap; not a disk partition", "Configured"], True),
            (EncryptionState(), missing, ["Unknown", "Unknown", "Unknown", "Unknown"], False),
        )
        with patch.object(app.DriverCenterWindow, "refresh"), patch.object(app.FirmwareManager, "start"):
            window = app.DriverCenterWindow(self.application)
            try:
                for state, recovery, expected, navigation in cases:
                    with self.subTest(state=state):
                        with patch.object(window, "_add_state_row", wraps=window._add_state_row) as add_row:
                            page = create_encryption_page(window, state, recovery)
                        self.assertEqual([call.args[2] for call in add_row.call_args_list], expected)
                        self.assertTrue(all(call.args[3] is not False for call in add_row.call_args_list))
                        buttons = []

                        def visit(widget):
                            if isinstance(widget, Gtk.Button):
                                buttons.append(widget)
                            child = widget.get_first_child()
                            while child:
                                visit(child)
                                child = child.get_next_sibling()

                        visit(page)
                        self.assertEqual([button.get_label() for button in buttons],
                                         ["Recovery Environment"] if navigation else [])
                        if navigation:
                            with patch.object(window, "_select_page") as select:
                                buttons[0].emit("clicked")
                                select.assert_called_once_with("recovery")
            finally:
                window.destroy()

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

    def test_graphics_subtitle_reports_driver_state_without_vendor_information(self):
        device = HardwareDevice("gpu", "", "", driver_state_known=True,
                                active_driver="nouveau", active_driver_healthy=True)
        option = DriverOption("nvidia-driver", "fixture", recommended=True, installed=True)
        cases = (
            (device, "Ready"),
            (replace(device, vendor="NVIDIA"), "Ready"),
            (replace(device, driver_state_known=False), "Unknown"),
            (replace(device, active_driver_healthy=None), "Unknown"),
            (replace(device, active_driver=None, active_driver_healthy=False), "Support needs attention"),
            (replace(device, restart_required=True, active_driver_healthy=False), "Reboot Required"),
            (replace(device, options=(replace(option, installed=False),)), "Not installed"),
            (replace(device, options=(replace(option, update_available=True),)), "Update available"),
            (replace(device, active_driver_healthy=False,
                     options=(replace(option, update_available=True),)), "Support needs attention"),
        )
        with patch.object(app, "_", side_effect=lambda text: text):
            for state, expected in cases:
                with self.subTest(expected=expected, state=state):
                    self.assertEqual(app.DriverCenterWindow._graphics_navigation_summary(state),
                                     f"Driver status: {expected}")

    def test_initial_scan_and_rescan_build_all_pages_and_preserve_selection(self):
        scan = (
            GraphicsScan(devices=(HardwareDevice("gpu", "", ""),)),
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
                for selected in ("home", "graphics-0", "secure-boot", "encryption", "recovery"):
                    with self.subTest(selected=selected):
                        window._selected_page_name = selected
                        window._show_loading()
                        self.assertEqual(window.stack.get_visible_child_name(), "loading")
                        self.assertEqual(window._apply_scan(*scan), GLib.SOURCE_REMOVE)
                        self.assertEqual(window.stack.get_visible_child_name(), selected)
                        expected = ("home", "graphics-0", "audio", "printing", "xbox", "secure-boot",
                                    "encryption", "recovery", "firmware", "computer")
                        self.assertEqual(window.stack.get_pages().get_n_items(), len(expected))
                        for index, page_name in enumerate(expected):
                            self.assertIsNotNone(window.stack.get_child_by_name(page_name))
                            self.assertEqual(window.device_list.get_row_at_index(index).page_name,
                                             page_name)
                        self.assertIsNone(window.device_list.get_row_at_index(len(expected)))
                        secure_row = window.device_list.get_row_at_index(5)
                        self.assertEqual(secure_row.subtitle_label.get_label(), "Disabled")
                        graphics_row = window.device_list.get_row_at_index(1)
                        self.assertEqual(graphics_row.subtitle_label.get_label(), "Driver status: Unknown")
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

    def test_intel_overview_requires_every_gpu_to_have_a_native_driver(self):
        from anduinos_driver_center.intel_graphics import IntelDevice, IntelSnapshot

        secure_boot = SecureBootState(False, True, True, True, "serial")
        audio = AudioState(PackageState("firmware-sof-signed", True, "1"),
                           PackageState("alsa-ucm-conf", True, "1"), True, True, (), ())
        printing = PrintingState(True, True, (), (), None, (), (), (), ())
        xbox = XboxState(XboxStatus.NOT_INSTALLED, False, False, False, None, False)
        cases = (
            (("i915",), None, "Ready", "success-pill"),
            (("xe",), None, "Ready", "success-pill"),
            (("i915", "xe"), None, "Ready", "success-pill"),
            (("",), None, "Kernel module", "installed-pill"),
            (("vfio-pci",), None, "Kernel module", "installed-pill"),
            (("i915", ""), None, "Kernel module", "installed-pill"),
            (("i915",), "Scan failed", "Action required", "warning-pill"),
        )
        with patch.object(app.DriverCenterWindow, "refresh"), \
             patch.object(app.FirmwareManager, "start"), \
             patch.object(app, "_", side_effect=lambda text: text):
            window = app.DriverCenterWindow(self.application)
            try:
                for drivers, error, expected, css_class in cases:
                    with self.subTest(drivers=drivers, error=error):
                        devices = [IntelDevice(f"0000:00:0{index}.0", "1234", "Intel Graphics",
                                               driver, "", False)
                                   for index, driver in enumerate(drivers)]
                        scan = GraphicsScan(intel=IntelSnapshot(devices=devices), error=error)
                        with patch.object(window, "_overview_card", wraps=window._overview_card) as card:
                            page = window._home_page(scan, secure_boot, xbox, audio, printing, DkmsState())
                        self.assertEqual(card.call_args_list[0].args[2], expected)
                        self.assertEqual(card.call_args_list[0].args[4], css_class)
                        if not error:
                            self.assertEqual(card.call_args_list[0].args[5], "intel-graphics")
                        labels = []

                        def visit(widget):
                            if isinstance(widget, Gtk.Label) and widget.has_css_class(css_class):
                                labels.append(widget.get_label())
                            child = widget.get_first_child()
                            while child:
                                visit(child)
                                child = child.get_next_sibling()

                        visit(page)
                        self.assertIn(expected, labels)
            finally:
                window.destroy()

    def test_narrow_window_split_view_collapses_and_avoids_overflow(self):
        scan = (
            GraphicsScan(devices=(HardwareDevice("gpu", "NVIDIA RTX 4090", "PCI:10de:2684", driver_state_known=True, active_driver="nvidia", active_driver_healthy=True, options=(DriverOption("nvidia-driver-550", "NVIDIA driver", recommended=True, installed=True),)),)),
            SecureBootState(False, True, True, True, "serial", boot_loader="shim", setup_mode=False,
                            signing_identity=IdentityStatus(purpose="unified", enrollment="enrolled", key_present=True, key_valid=True, defaults_ready=True),
                            grub=GrubState(installed=True, signature="valid", enrollment="enrolled", deployment="ready", active_chain="trusted")),
            XboxState(XboxStatus.NOT_INSTALLED, False, False, False, None, False),
            DkmsState(),
            AudioState(PackageState("firmware-sof-anduinos", True, "1"), PackageState("alsa-ucm-conf-anduinos", True, "1"), True, True, (), ()),
            PrintingState(True, True, ("Office_Printer", "Design_Lab"), (), "Office_Printer", (), (), (), ()),
            RecoveryState(True, ("/dev/vda4",), PackageState("anduinos-recovery-env", True, "2.0.5-6+resolute"), "2.0.5-6+resolute", True, True),
        )
        with patch.object(app.DriverCenterWindow, "refresh"), \
             patch.object(app.FirmwareManager, "start"), \
             patch.object(app.ComputerPage, "reload"):
            window = app.DriverCenterWindow(self.application)
            try:
                self.assertFalse(window.stack.get_hhomogeneous())
                self.assertEqual(window.get_size_request(), (380, 500))
                window._apply_scan(*scan)
                split_min_w = window.split.get_preferred_size()[0].width
                self.assertLessEqual(split_min_w, 720)
                window.split.set_collapsed(True)
                window.split.set_show_sidebar(False)
                content_min_w = window.split.get_content().get_preferred_size()[0].width
                self.assertLessEqual(content_min_w, 385)
            finally:
                window.destroy()
