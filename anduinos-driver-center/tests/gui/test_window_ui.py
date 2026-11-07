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
            (replace(encrypted, root=VolumeState("/dev/vda1", "ext4", False)),
             missing, ["Not encrypted · ext4", "Not encrypted · ext4",
                       "Unencrypted disk swap is active", "Memory-based swap; not a disk partition",
                       "Not applicable to ext4. Use Rescue Center from the Live USB."], False),
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
                                         (["Recovery Environment"] if navigation else [])
                                         + (["Open", "Open", "Open"] if state.root.encrypted is True else []))
                        labels = []
                        def collect_labels(widget):
                            if isinstance(widget, Gtk.Label):
                                labels.append(widget.get_label())
                            child = widget.get_first_child()
                            while child:
                                collect_labels(child)
                                child = child.get_next_sibling()
                        collect_labels(page)
                        reinstall = [text for text in labels if "Reinstall using the latest" in text]
                        self.assertEqual(bool(reinstall), state.root.encrypted is False)
                        if reinstall:
                            self.assertIn("not supported yet", reinstall[0])
                        if navigation:
                            with patch.object(window, "_select_page") as select:
                                buttons[0].emit("clicked")
                                select.assert_called_once_with("recovery")
            finally:
                window.destroy()

    def test_password_and_recovery_forms_validate_before_sending_secrets(self):
        from anduinos_driver_center.encryption_ui import CredentialDialog
        import json
        with patch.object(app.DriverCenterWindow, "refresh"), patch.object(app.FirmwareManager, "start"):
            window = app.DriverCenterWindow(self.application)
            try:
                for role in ("password", "recovery"):
                    with self.subTest(role=role), \
                         patch("anduinos_driver_center.encryption_ui.threading.Thread") as thread, \
                         patch("anduinos_driver_center.encryption_ui.GLib.idle_add", side_effect=lambda fn, *args: fn(*args)), \
                         patch("anduinos_driver_center.encryption_ui.subprocess.run", return_value=Mock(returncode=0, stdout=json.dumps({"managed": True, "pending": False}))) as run:
                        thread.side_effect = lambda *, target, **kwargs: Mock(start=target)
                        dialog = CredentialDialog(window, role)
                        try:
                            self.assertFalse(dialog.apply.get_sensitive())
                            dialog.current.set_text("old-password")
                            if role == "password":
                                dialog.replacement.set_text("new-password")
                                dialog.confirmation.set_text("different")
                                self.assertFalse(dialog.apply.get_sensitive())
                                dialog.confirmation.set_text("new-password")
                            else:
                                self.assertEqual(len(dialog.replacement.get_text()), 48)
                                self.assertFalse(dialog.apply.get_sensitive())
                                dialog.saved.set_active(True)
                            self.assertTrue(dialog.apply.get_sensitive())
                            run.return_value = Mock(returncode=1, stdout=json.dumps({"error": "wrong-password"}), stderr="private diagnostic")
                            dialog.apply.emit("clicked")
                            command = run.call_args.args[0]
                            self.assertEqual(command, ["pkexec", "/usr/libexec/anduinos-driver-center/driver-helper", "encryption-change"])
                            self.assertNotIn("old-password", " ".join(command))
                            self.assertEqual(json.loads(run.call_args.kwargs["input"])["current"], "old-password")
                            self.assertIn("incorrect", dialog.message.get_label())
                            self.assertNotIn("private diagnostic", dialog.message.get_label())
                            self.assertFalse(window._operation_running)
                        finally:
                            dialog.clear()
                            dialog.destroy()
            finally:
                window.destroy()

    def test_slow_credential_operation_keeps_progress_visible_and_releases_every_outcome(self):
        import json
        import threading
        import time
        from anduinos_driver_center.encryption_ui import CredentialDialog

        def pump_until(predicate):
            deadline = time.monotonic() + 5
            while not predicate() and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            self.assertTrue(predicate())

        for code, response in ((0, {}), (1, {"error": "wrong-password"}), (126, {})):
            with self.subTest(code=code), \
                 patch.object(app.DriverCenterWindow, "refresh"), \
                 patch.object(app.FirmwareManager, "start"):
                release = threading.Event()
                started = threading.Event()
                changes = []

                def helper(command, **kwargs):
                    if command[-1] == "encryption-inspect":
                        return Mock(returncode=0, stdout='{"managed": true}')
                    changes.append(command)
                    started.set()
                    release.wait(5)
                    return Mock(returncode=code, stdout=json.dumps(response))

                with patch("anduinos_driver_center.encryption_ui.subprocess.run", side_effect=helper):
                    window = app.DriverCenterWindow(self.application)
                    window.refresh.reset_mock()
                    dialog = CredentialDialog(window, "password")
                    try:
                        pump_until(lambda: dialog.ready)
                        dialog.current.set_text("old-password")
                        dialog.replacement.set_text("new-password")
                        dialog.confirmation.set_text("new-password")
                        dialog.apply.emit("clicked")
                        pump_until(started.is_set)
                        progress = dialog.operation
                        self.assertTrue(progress.get_visible())
                        self.assertTrue(progress.get_modal())
                        self.assertTrue(progress.emit("close-request"))
                        self.assertTrue(dialog.emit("close-request"))
                        self.assertTrue(window.emit("close-request"))
                        self.assertFalse(dialog.apply.get_sensitive())
                        self.assertFalse(dialog.cancel.get_sensitive())
                        self.assertLess(progress.progress.get_fraction(), 1)
                        buffer = progress.log.get_buffer()
                        self.assertEqual(buffer.get_char_count(), 0)
                        # A UI timer still runs while the privileged boundary waits.
                        ticks = []
                        GLib.timeout_add(30, lambda: ticks.append(True) and False)
                        pump_until(lambda: bool(ticks))
                        self.assertTrue(dialog.busy)
                        dialog.submit(None)
                        self.assertEqual(len(changes), 1)
                        release.set()
                        pump_until(lambda: not dialog.busy)
                        self.assertIsNone(dialog.operation)
                        self.assertFalse(window._operation_running)
                        self.assertFalse(window.emit("close-request"))
                        if code:
                            self.assertTrue(dialog.apply.get_sensitive())
                            self.assertTrue(dialog.cancel.get_sensitive())
                            self.assertIn("incorrect" if code == 1 else "cancelled",
                                          dialog.message.get_label())
                        else:
                            self.assertEqual(dialog.current.get_text(), "")
                            window.refresh.assert_called_once()
                    finally:
                        release.set()
                        pump_until(lambda: not dialog.busy)
                        dialog.destroy()
                        window.destroy()

    def test_emergency_reset_form_needs_key_and_new_password_only(self):
        from anduinos_driver_center.encryption_ui import CredentialDialog
        import json
        with patch.object(app.DriverCenterWindow, "refresh"), patch.object(app.FirmwareManager, "start"):
            window = app.DriverCenterWindow(self.application)
            try:
                for managed in (False, True):
                    with self.subTest(managed=managed), \
                         patch("anduinos_driver_center.encryption_ui.threading.Thread") as thread, \
                         patch("anduinos_driver_center.encryption_ui.GLib.idle_add", side_effect=lambda fn, *args: fn(*args)), \
                         patch("anduinos_driver_center.encryption_ui.subprocess.run", return_value=Mock(returncode=0, stdout=json.dumps({"managed": managed, "pending": False}))) as run:
                        thread.side_effect = lambda *, target, **kwargs: Mock(start=target)
                        dialog = CredentialDialog(window, "password", "recovery")
                        try:
                            self.assertEqual(dialog.current.get_title(), "Emergency recovery key")
                            self.assertFalse(dialog.recovery.get_visible())
                            self.assertIn("do not need the forgotten password", dialog.message.get_label())
                            dialog.current.set_text("a" * 48)
                            dialog.replacement.set_text("new-password")
                            dialog.confirmation.set_text("new-password")
                            self.assertTrue(dialog.apply.get_sensitive())
                            run.return_value = Mock(returncode=1, stdout=json.dumps({"error": "wrong-recovery-key"}))
                            dialog.apply.emit("clicked")
                            payload = json.loads(run.call_args.kwargs["input"])
                            self.assertEqual(payload["authentication"], "recovery")
                            self.assertEqual(payload["current"], "a" * 48)
                            self.assertEqual(payload["legacy_recovery"], "")
                            self.assertNotIn("a" * 48, " ".join(run.call_args.args[0]))
                            self.assertIn("emergency recovery key is incorrect", dialog.message.get_label())
                            self.assertFalse(window._operation_running)
                        finally:
                            dialog.clear()
                            dialog.destroy()
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
                        def descendants(widget):
                            yield widget
                            child = widget.get_first_child()
                            while child:
                                yield from descendants(child)
                                child = child.get_next_sibling()

                        for target, title in (("recovery", "Recovery Environment"),
                                              ("encryption", "Disk Encryption")):
                            home = window.stack.get_child_by_name("home")
                            card = next(widget for widget in descendants(home)
                                        if isinstance(widget, Gtk.Button)
                                        and any(isinstance(label, Gtk.Label)
                                                and label.get_label() == title
                                                for label in descendants(widget)))
                            card.emit("clicked")
                            self.assertEqual(window.stack.get_visible_child_name(), target)
                        window._select_page(selected)
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
                            page = window._home_page(
                                scan, secure_boot, xbox, audio, printing, DkmsState(),
                                RecoveryState(False, (), PackageState("anduinos-recovery-env", False, None),
                                              None, False, False),
                                app.EncryptionState(),
                            )
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
