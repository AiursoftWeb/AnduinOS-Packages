"""GTK4/libadwaita frontend for AnduinOS Driver Center."""

from __future__ import annotations

import gettext
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .core import (
    AudioState,
    DkmsState,
    GraphicsScan,
    HardwareDevice,
    PackageState,
    PrintingState,
    SecureBootState,
    XboxState,
    XboxStatus,
    scan_system,
)
from .firmware import (
    FirmwareDevice,
    FirmwareManager,
    FirmwareSnapshot,
)

try:
    from anduinos_secureboot.ui import create_secure_boot_page
except ModuleNotFoundError:
    import sys
    _toolkit_src = Path(__file__).resolve().parents[3] / "anduinos-secureboot-toolkit" / "src"
    sys.path.insert(0, str(_toolkit_src))
    from anduinos_secureboot.ui import create_secure_boot_page


APP_ID = "com.anduinos.DriverCenter"
HELPER = "/usr/libexec/anduinos-driver-center/driver-helper"
HELPER_SUCCESS_MESSAGE = "Driver operation completed successfully."


def _command_output_summary(output: str, marker: str) -> str | None:
    """Return the last output line emitted by one command in the helper log."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    marker_indexes = [index for index, line in enumerate(lines) if line == marker]
    if not marker_indexes:
        return None
    command_output = lines[marker_indexes[-1] + 1:]
    if command_output and command_output[-1] == HELPER_SUCCESS_MESSAGE:
        command_output.pop()
    return command_output[-1] if command_output else None
LOCALE_DIR = "/usr/share/locale"
gettext.bindtextdomain("anduinos-driver-center", LOCALE_DIR)
gettext.textdomain("anduinos-driver-center")
_ = gettext.gettext


def _resource_path(name: str) -> Path:
    installed = Path("/usr/share/anduinos-driver-center/illustrations", name)
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[2] / "resources" / name


def _status_icon(name: str, css_class: str) -> Gtk.Image:
    icon = Gtk.Image.new_from_icon_name(name)
    icon.set_pixel_size(18)
    icon.add_css_class(css_class)
    return icon


def _pill(text: str, css_class: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_halign(Gtk.Align.END)
    label.set_valign(Gtk.Align.CENTER)
    label.set_vexpand(False)
    label.add_css_class("caption")
    label.add_css_class("status-pill")
    label.add_css_class(css_class)
    return label


def _illustration(name: str) -> Gtk.Picture:
    picture = Gtk.Picture.new_for_filename(str(_resource_path(name)))
    picture.set_content_fit(Gtk.ContentFit.CONTAIN)
    picture.set_can_shrink(True)
    picture.set_size_request(112, 112)
    picture.set_halign(Gtk.Align.END)
    picture.set_valign(Gtk.Align.CENTER)
    return picture


def _large_app_icon(width: int = 260, height: int = 180) -> Gtk.Image:
    icon = Gtk.Image.new_from_icon_name(APP_ID)
    icon.set_pixel_size(144)
    icon.set_size_request(width, height)
    icon.set_halign(Gtk.Align.CENTER)
    icon.set_valign(Gtk.Align.CENTER)
    return icon


def _scrolled_window(**properties) -> Gtk.ScrolledWindow:
    """Return a vertical scroller whose scrollbar stays visible when needed."""

    properties.setdefault("hscrollbar_policy", Gtk.PolicyType.NEVER)
    properties.setdefault("vscrollbar_policy", Gtk.PolicyType.AUTOMATIC)
    scroll = Gtk.ScrolledWindow(**properties)
    scroll.set_overlay_scrolling(False)
    return scroll


class DriverCenterWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=_("AnduinOS Driver Center"))
        self.set_default_size(1250, 810)
        self.set_size_request(720, 520)
        self._graphics: list[HardwareDevice] = []
        self._secure_boot: SecureBootState | None = None
        self._xbox: XboxState | None = None
        self._dkms: DkmsState | None = None
        self._audio: AudioState | None = None
        self._printing: PrintingState | None = None
        self._graphics_scan = GraphicsScan()
        self._selected_package: str | None = None
        self._selected_page_name: str | None = "home"
        self._rebuilding_navigation = False
        self._firmware_row: Gtk.ListBoxRow | None = None
        self._firmware_progress: Gtk.ProgressBar | None = None
        self._firmware_progress_label: Gtk.Label | None = None
        self._firmware_request_label: Gtk.Label | None = None
        self._firmware_manager = FirmwareManager(
            self._firmware_state_changed,
            self._firmware_progress_changed,
            self._firmware_operation_done,
            self._firmware_request_received,
        )

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .status-pill {
                border-radius: 999px;
                padding: 3px 9px;
                font-weight: 600;
            }
            .recommended-pill {
                color: @accent_color;
                background-color: alpha(@accent_color, 0.15);
            }
            .in-use-pill {
                color: @success_color;
                background-color: alpha(@success_color, 0.15);
            }
            .installed-pill {
                color: @window_fg_color;
                background-color: alpha(@window_fg_color, 0.10);
            }
            .success-pill {
                color: @success_color;
                background-color: alpha(@success_color, 0.14);
            }
            .warning-pill {
                color: @warning_color;
                background-color: alpha(@warning_color, 0.14);
            }
            .overview-card {
                padding: 0;
            }
            .overview-card:hover {
                background-color: alpha(@accent_color, 0.10);
            }
            .hero-card {
                padding: 0;
            }
            list.navigation-list {
                background: transparent;
            }
            list.navigation-list row {
                border: none;
                border-radius: 10px;
                margin: 2px 0;
                outline: none;
                box-shadow: none;
            }
            list.navigation-list row:hover {
                background-color: alpha(@view_fg_color, 0.07);
            }
            list.navigation-list row:selected {
                background-color: alpha(@accent_color, 0.28);
                outline: none;
                box-shadow: none;
            }
            .driver-footer {
                border-top: 1px solid alpha(@borders, 0.7);
                background-color: alpha(@window_bg_color, 0.96);
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.split = Adw.OverlaySplitView()
        self.split.set_min_sidebar_width(220)
        self.split.set_max_sidebar_width(290)
        self.split.set_sidebar_width_fraction(0.28)
        self.set_content(self.split)

        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_header.set_title_widget(
            Adw.WindowTitle.new(_("AnduinOS Driver Center"), "AnduinOS")
        )
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_toolbar.add_top_bar(sidebar_header)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.set_margin_top(12)
        self.sidebar.set_margin_bottom(12)
        self.sidebar.set_margin_start(12)
        self.sidebar.set_margin_end(12)
        self.device_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.device_list.add_css_class("navigation-list")
        self.device_list.connect("row-selected", self._row_selected)
        self.sidebar.append(self.device_list)
        sidebar_scroll = _scrolled_window()
        sidebar_scroll.set_child(self.sidebar)
        sidebar_toolbar.set_content(sidebar_scroll)
        self.split.set_sidebar(sidebar_toolbar)

        content_toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.page_title = Adw.WindowTitle.new(_("Home"), "")
        header.set_title_widget(self.page_title)
        self.refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Scan again")
        )
        self.refresh_button.connect("clicked", lambda _button: self.refresh())
        header.pack_end(self.refresh_button)
        menu = Gio.Menu()
        menu.append(_("About Driver Center"), "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_tooltip_text(_("Main Menu"))
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.connect(
            "toggled",
            lambda button: self.split.set_show_sidebar(button.get_active()),
        )
        header.pack_start(self.sidebar_toggle)
        content_toolbar.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        content_toolbar.set_content(self.stack)
        self.split.set_content(content_toolbar)
        self.split.connect("notify::show-sidebar", self._sync_sidebar_controls)
        self.split.connect("notify::collapsed", self._sync_sidebar_controls)
        self._sync_sidebar_controls()

        compact = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 700px")
        )
        compact.add_setter(self.split, "collapsed", True)
        compact.add_setter(self.split, "show-sidebar", False)
        self.add_breakpoint(compact)
        self._show_loading()
        self.refresh()
        self._firmware_manager.start()

    def _sync_sidebar_controls(self, *_args) -> None:
        self.sidebar_toggle.set_visible(self.split.get_collapsed())
        if self.sidebar_toggle.get_active() != self.split.get_show_sidebar():
            self.sidebar_toggle.set_active(self.split.get_show_sidebar())

    def _clear(self, widget: Gtk.Widget) -> None:
        child = widget.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            widget.remove(child)
            child = next_child

    def _show_loading(self) -> None:
        self._clear(self.stack)
        self.page_title.set_title(_("AnduinOS Driver Center"))
        status = Adw.StatusPage(
            title=_("Scanning for drivers"),
            description=_("Checking hardware and Secure Boot status…"),
        )
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(48, 48)
        status.set_child(spinner)
        self.stack.add_named(status, "loading")
        self.stack.set_visible_child_name("loading")

    def refresh(self) -> None:
        self.refresh_button.set_sensitive(False)
        self.device_list.set_sensitive(False)
        self._rebuilding_navigation = True
        self._show_loading()

        def worker() -> None:
            result = scan_system()
            GLib.idle_add(self._apply_scan, *result)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_scan(
        self,
        graphics_scan: GraphicsScan,
        secure_boot: SecureBootState,
        xbox: XboxState,
        dkms: DkmsState,
        audio: AudioState,
        printing: PrintingState,
    ) -> bool:
        graphics = list(graphics_scan.devices)
        self._graphics_scan = graphics_scan
        self._graphics = graphics
        self._secure_boot = secure_boot
        self._xbox = xbox
        self._dkms = dkms
        self._audio = audio
        self._printing = printing
        self.refresh_button.set_sensitive(True)
        self.device_list.set_sensitive(True)
        self._clear(self.device_list)
        self._clear(self.stack)

        home_row = self._device_row("go-home-symbolic", _("Home"), _("System status"))
        home_row.page_name = "home"
        home_row.page_title = _("Home")
        self.device_list.append(home_row)
        self.stack.add_named(
            self._home_page(graphics_scan, secure_boot, xbox, audio, printing),
            "home",
        )

        for index, device in enumerate(graphics):
            label = device.title
            subtitle = device.vendor
            row = self._device_row("video-display-symbolic", label, subtitle)
            row.page_name = f"graphics-{index}"
            row.page_title = device.title
            self.device_list.append(row)
            self.stack.add_named(self._graphics_page(device, secure_boot), row.page_name)

        audio_row = self._device_row(
            "audio-card-symbolic", _("Audio"),
            _("Audio support ready") if audio.ready else _("Support needs attention"),
        )
        audio_row.page_name = "audio"
        audio_row.page_title = _("Audio")
        self.device_list.append(audio_row)
        self.stack.add_named(self._audio_page(audio), "audio")

        printer_count = len(printing.printers)
        if not printing.service_running:
            printing_subtitle = (
                _("Printing service stopped")
                if printing.startup_enabled
                else _("Printing support disabled.")
            )
        elif printing.missing_required_packages:
            printing_subtitle = _("Support needs attention")
        elif printing.disabled_printers:
            printing_subtitle = _("Some queues are paused")
        elif not printing.printers:
            printing_subtitle = _("No printers configured")
        else:
            printing_subtitle = gettext.ngettext(
                "%d printer configured",
                "%d printers configured",
                printer_count,
            ) % printer_count
        printing_row = self._device_row(
            "printer-symbolic", _("Printers"), printing_subtitle
        )
        printing_row.page_name = "printing"
        printing_row.page_title = _("Printers")
        self.device_list.append(printing_row)
        self.stack.add_named(self._printing_page(printing), "printing")

        xbox_row = self._device_row(
            "input-gaming-symbolic", _("Xbox Controller"),
            (
                _("xpadneo installed")
                if xbox.status in {XboxStatus.LOADED, XboxStatus.READY}
                else (
                    _("Optional Bluetooth driver")
                    if xbox.status is XboxStatus.NOT_INSTALLED
                    else _("Support needs attention")
                )
            ),
        )
        xbox_row.page_name = "xbox"
        xbox_row.page_title = _("Xbox Controller")
        self.device_list.append(xbox_row)
        self.stack.add_named(self._xbox_page(xbox, secure_boot), "xbox")

        # Secure Boot management is irrelevant when firmware enforcement is
        # disabled.  Keep the device workflow uncluttered and, importantly,
        # do not turn MOK or signing configuration into an install gate.
        if not secure_boot.enforcement_inactive:
            secure_row = self._device_row(
                "security-high-symbolic", _("Secure Boot"),
                _("Trust established") if secure_boot.ready else _("Action required"),
            )
            secure_row.page_name = "secure-boot"
            secure_row.page_title = _("Secure Boot")
            self.device_list.append(secure_row)
            self.stack.add_named(self._secure_boot_page(secure_boot, dkms), "secure-boot")

        firmware_snapshot = self._firmware_manager.snapshot
        firmware_row = self._device_row(
            "application-x-firmware-symbolic",
            _("Device Firmware"),
            self._firmware_navigation_summary(firmware_snapshot),
        )
        firmware_row.page_name = "firmware"
        firmware_row.page_title = _("Device Firmware")
        self._firmware_row = firmware_row
        self.device_list.append(firmware_row)
        self.stack.add_named(self._firmware_page(firmware_snapshot), "firmware")

        selected = None
        row = self.device_list.get_row_at_index(0)
        while row:
            if getattr(row, "page_name", None) == self._selected_page_name:
                selected = row
                break
            row = self.device_list.get_row_at_index(row.get_index() + 1)
        selected = selected or self.device_list.get_row_at_index(0)
        self._rebuilding_navigation = False
        if selected:
            self.device_list.select_row(selected)
        return GLib.SOURCE_REMOVE

    def _device_row(self, icon_name: str, title: str, subtitle: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(28)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=title, xalign=0, ellipsize=3)
        name.add_css_class("heading")
        detail = Gtk.Label(label=subtitle, xalign=0, ellipsize=3)
        detail.add_css_class("dim-label")
        labels.append(name); labels.append(detail)
        box.append(icon); box.append(labels)
        row.set_child(box)
        row.subtitle_label = detail
        return row

    def _row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._rebuilding_navigation:
            return
        if row and hasattr(row, "page_name"):
            self._selected_page_name = row.page_name
            self.stack.set_visible_child_name(row.page_name)
            self.page_title.set_title(getattr(row, "page_title", row.page_name))
            if self.split.get_collapsed():
                self.split.set_show_sidebar(False)

    def _select_page(self, page_name: str) -> None:
        row = self.device_list.get_row_at_index(0)
        while row:
            if getattr(row, "page_name", None) == page_name:
                self.device_list.select_row(row)
                return
            row = self.device_list.get_row_at_index(row.get_index() + 1)

    def _replace_stack_page(self, name: str, page: Gtk.Widget) -> None:
        old_page = self.stack.get_child_by_name(name)
        if old_page is None:
            return
        was_visible = self.stack.get_visible_child_name() == name
        self.stack.remove(old_page)
        self.stack.add_named(page, name)
        if was_visible:
            self.stack.set_visible_child_name(name)

    def _firmware_navigation_summary(self, state: FirmwareSnapshot) -> str:
        if state.loading and not state.devices:
            return _("Checking for updates…")
        if state.error:
            return _("Support needs attention")
        if state.shutdown_required:
            return _("Shutdown Required")
        if state.restart_required:
            return _("Reboot Required")
        update_count = len(state.updates)
        if update_count:
            return gettext.ngettext(
                "%d firmware update available",
                "%d firmware updates available",
                update_count,
            ) % update_count
        if not state.devices:
            return _("No supported devices")
        return _("Firmware is up to date")

    def _firmware_card_state(
        self, state: FirmwareSnapshot
    ) -> tuple[str, str, str]:
        if state.loading and not state.devices:
            return _("Checking…"), _("Contacting the firmware service"), "installed-pill"
        if state.error:
            return _("Needs attention"), state.error, "warning-pill"
        if state.shutdown_required:
            return _("Shutdown Required"), _("Shutdown required after installation"), "warning-pill"
        if state.restart_required:
            return _("Reboot Required"), _("Restart to finish the firmware update"), "warning-pill"
        update_count = len(state.updates)
        if update_count:
            subtitle = gettext.ngettext(
                "%d firmware update available",
                "%d firmware updates available",
                update_count,
            ) % update_count
            return _("Update available"), subtitle, "recommended-pill"
        if not state.devices:
            return _("Not detected"), _("No supported firmware devices"), "installed-pill"
        device_count = len(state.devices)
        subtitle = gettext.ngettext(
            "%d device is up to date",
            "%d devices are up to date",
            device_count,
        ) % device_count
        return _("Ready"), subtitle, "success-pill"

    def _firmware_state_changed(self, state: FirmwareSnapshot) -> None:
        if self._firmware_row is not None:
            self._firmware_row.subtitle_label.set_label(
                self._firmware_navigation_summary(state)
            )
        if self.stack.get_child_by_name("firmware") is not None:
            self._replace_stack_page("firmware", self._firmware_page(state))
        if (
            self.stack.get_child_by_name("home") is not None
            and self._secure_boot is not None
            and self._xbox is not None
            and self._audio is not None
            and self._printing is not None
        ):
            self._replace_stack_page(
                "home",
                self._home_page(
                    self._graphics_scan,
                    self._secure_boot,
                    self._xbox,
                    self._audio,
                    self._printing,
                ),
            )

    def _firmware_progress_changed(self, status: int, percentage: int) -> None:
        status_messages = {
            7: _("Scheduling firmware update…"),
            8: _("Downloading firmware…"),
            5: _("Installing firmware…"),
            6: _("Verifying firmware…"),
            11: _("Waiting for authentication…"),
            14: _("Waiting for user action…"),
        }
        if self._firmware_progress_label is not None:
            self._firmware_progress_label.set_label(
                status_messages.get(status, _("Updating firmware…"))
            )
        if self._firmware_progress is not None:
            self._firmware_progress.set_fraction(max(0, min(percentage, 100)) / 100)
            self._firmware_progress.set_text(f"{percentage}%")

    def _firmware_request_received(self, message: str) -> None:
        if self._firmware_request_label is not None:
            self._firmware_request_label.set_label(message)
            self._firmware_request_label.set_visible(True)
        self._toast(message)

    def _firmware_operation_done(
        self,
        action: str,
        success: bool,
        message: str | None,
        restart_required: bool,
        shutdown_required: bool,
    ) -> None:
        if not success:
            body = message or _("unknown error")
            if action == "update" and (restart_required or shutdown_required):
                body += "\n\n" + (
                    _("Shutdown required after installation")
                    if shutdown_required
                    else _("Restart required after installation")
                )
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading=_("Firmware operation failed"),
                body=body,
            )
            dialog.add_response("ok", _("OK"))
            if action == "update" and (restart_required or shutdown_required):
                dialog.connect(
                    "response",
                    lambda *_args: self._firmware_restart_dialog(
                        shutdown_required
                    ),
                )
            dialog.present()
            return
        if action == "refresh":
            self._toast(_("Firmware metadata refreshed."))
            return
        if action == "check":
            update_count = len(self._firmware_manager.snapshot.updates)
            self._toast(
                gettext.ngettext(
                    "%d firmware update available",
                    "%d firmware updates available",
                    update_count,
                ) % update_count
                if update_count
                else _("No firmware updates are available.")
            )
            return
        if action == "update" and (restart_required or shutdown_required):
            self._firmware_restart_dialog(shutdown_required)
            return
        if action == "update":
            self._toast(_("Firmware update completed."))

    def _firmware_restart_dialog(self, shutdown_required: bool) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Shutdown Required") if shutdown_required else _("Restart Required"),
            body=(
                _(
                    "The firmware update has been prepared. Save your work and shut "
                    "down the computer to finish installing it."
                )
                if shutdown_required
                else _(
                    "The firmware update has been prepared. Save your work and restart "
                    "the computer to finish installing it."
                )
            ),
        )
        dialog.add_response("later", _("Later"))
        dialog.add_response(
            "restart",
            _("Shut Down Now") if shutdown_required else _("Restart Now"),
        )
        dialog.set_default_response("later")
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _dialog, response: self._firmware_power_action(shutdown_required)
            if response == "restart"
            else None,
        )
        dialog.present()

    def _firmware_power_action(self, shutdown_required: bool) -> None:
        try:
            process = Gio.Subprocess.new(
                ["systemctl", "poweroff" if shutdown_required else "reboot"],
                Gio.SubprocessFlags.NONE,
            )
            process.wait_check_async(None, self._firmware_power_action_done)
        except GLib.Error as error:
            self._toast(_("Firmware operation failed") + ": " + str(error))

    def _firmware_power_action_done(self, process, result) -> None:
        try:
            process.wait_check_finish(result)
        except GLib.Error as error:
            self._toast(_("Firmware operation failed") + ": " + str(error))

    def _page_shell(
        self, title: str, description: str, illustration: str | None = None
    ) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
        scroll = _scrolled_window()
        clamp = Adw.Clamp(maximum_size=650, tightening_threshold=500)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(32); content.set_margin_bottom(32)
        content.set_margin_start(24); content.set_margin_end(24)
        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        hero_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero_text.set_hexpand(True)
        hero_text.set_valign(Gtk.Align.CENTER)
        heading = Gtk.Label(label=title, xalign=0, wrap=True)
        heading.add_css_class("title-1")
        intro = Gtk.Label(label=description, xalign=0, wrap=True)
        intro.add_css_class("dim-label")
        hero_text.append(heading)
        hero_text.append(intro)
        hero.append(hero_text)
        if illustration:
            hero.append(_illustration(illustration))
        content.append(hero)
        clamp.set_child(content); scroll.set_child(clamp)
        return scroll, content

    @staticmethod
    def _recommended_driver(device: HardwareDevice):
        return next((option for option in device.options if option.recommended), None)

    def _home_page(
        self,
        graphics_scan: GraphicsScan,
        secure_boot: SecureBootState,
        xbox: XboxState,
        audio: AudioState,
        printing: PrintingState,
    ) -> Gtk.Widget:
        scroll = _scrolled_window()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content.set_margin_top(24)
        content.set_margin_bottom(32)
        content.set_margin_start(24)
        content.set_margin_end(24)
        clamp = Adw.Clamp(maximum_size=980, tightening_threshold=760)
        clamp.set_child(content)
        scroll.set_child(clamp)

        recommendations = [
            (device, option)
            for device in graphics_scan.devices
            if (option := self._recommended_driver(device)) is not None
        ]
        missing = [item for item in recommendations if not item[1].installed]
        updates = [item for item in recommendations if item[1].update_available]
        waiting_for_restart = [
            item
            for item in recommendations
            if item[1].installed
            and not item[1].update_available
            and not item[1].active
            and item[0].driver_state_known
            and not (
                item[0].active_driver
                and item[0].active_driver.lower().replace("_", "-").startswith("nvidia")
            )
        ]
        unhealthy = [
            device
            for device in graphics_scan.devices
            if device.active_driver_healthy is False
        ]

        def graphics_page_name(device: HardwareDevice) -> str:
            return f"graphics-{graphics_scan.devices.index(device)}"

        def version_summary(option) -> str:
            details = [option.package]
            if option.installed_version:
                details.append(f'{_("Installed")}: {option.installed_version}')
            if option.candidate_version:
                details.append(f'{_("Available")}: {option.candidate_version}')
            return " · ".join(details)

        hero = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=2,
            min_children_per_line=1,
            column_spacing=24,
            row_spacing=12,
            homogeneous=False,
        )
        hero.add_css_class("card")
        hero.add_css_class("hero-card")
        artwork = _large_app_icon()
        artwork.set_margin_start(20)
        artwork.set_margin_end(20)
        artwork.set_margin_top(12)
        artwork.set_margin_bottom(12)
        hero.insert(artwork, -1)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        details.set_margin_start(20)
        details.set_margin_end(28)
        details.set_margin_top(28)
        details.set_margin_bottom(28)
        details.set_valign(Gtk.Align.CENTER)
        details.set_hexpand(True)

        action: Gtk.Button | None = None
        if graphics_scan.error:
            badge_text = _("Action required")
            badge_class = "warning-pill"
            title = _("Driver status")
            description = _("Driver operation failed: ") + graphics_scan.error
            action = Gtk.Button(label=_("Scan again"))
            action.connect("clicked", lambda _button: self.refresh())
        elif (missing or updates) and not secure_boot.ready:
            badge_text = _("Action required")
            badge_class = "warning-pill"
            title = _("Secure Boot")
            description = _(
                "Secure Boot status or trust must be resolved before installing a third-party driver."
            )
            action = Gtk.Button(label=_("Secure Boot"))
            action.connect("clicked", lambda _button: self._select_page("secure-boot"))
        elif missing:
            badge_text = _("Recommended")
            badge_class = "recommended-pill"
            title = missing[0][0].title
            description = _(
                "Choose the driver used by this device. AnduinOS marks the hardware-tested recommendation."
            )
            action = Gtk.Button(label=_("Apply Changes"))
            action.add_css_class("suggested-action")
            action.add_css_class("pill")
            action.connect(
                "clicked",
                lambda button: self._confirm_recommended_install(button),
            )
        elif updates:
            badge_text = _("Update available")
            badge_class = "recommended-pill"
            title = updates[0][0].title
            description = version_summary(updates[0][1])
            action = Gtk.Button(label=_("Apply Changes"))
            action.add_css_class("suggested-action")
            action.add_css_class("pill")
            action.connect(
                "clicked",
                lambda button: self._confirm_recommended_install(button),
            )
        elif waiting_for_restart:
            badge_text = _("Reboot Required")
            badge_class = "warning-pill"
            title = waiting_for_restart[0][0].title
            description = _("Driver changes completed. Restart may be required.")
        elif unhealthy:
            badge_text = _("Support needs attention")
            badge_class = "warning-pill"
            title = unhealthy[0].title
            description = unhealthy[0].active_driver_error or _("Support needs attention")
            action = Gtk.Button(label=_("Available drivers"))
            target = graphics_page_name(unhealthy[0])
            action.connect("clicked", lambda _button: self._select_page(target))
        else:
            badge_text = _("Ready")
            badge_class = "success-pill"
            title = (
                graphics_scan.devices[0].title
                if graphics_scan.devices
                else _("Driver status")
            )
            description = _("No additional drivers are needed.")

        badge = _pill(badge_text, badge_class)
        badge.set_halign(Gtk.Align.START)
        details.append(badge)
        heading = Gtk.Label(label=title, xalign=0, wrap=True)
        heading.add_css_class("title-1")
        details.append(heading)
        intro = Gtk.Label(label=description, xalign=0, wrap=True)
        intro.add_css_class("dim-label")
        details.append(intro)
        if action:
            action.set_halign(Gtk.Align.START)
            action.set_margin_top(8)
            details.append(action)
        hero.insert(details, -1)
        content.append(hero)

        section_title = Gtk.Label(label=_("System status"), xalign=0)
        section_title.add_css_class("title-2")
        content.append(section_title)

        cards = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=2,
            min_children_per_line=1,
            column_spacing=16,
            row_spacing=16,
            homogeneous=True,
        )

        if graphics_scan.error:
            graphics_state = _("Action required")
            graphics_subtitle = _("Not detected")
            graphics_class = "warning-pill"
            graphics_target = None
        elif missing:
            graphics_state = _("Recommended")
            graphics_subtitle = missing[0][0].title
            graphics_class = "recommended-pill"
            graphics_target = graphics_page_name(missing[0][0])
        elif updates:
            graphics_state = _("Update available")
            graphics_subtitle = version_summary(updates[0][1])
            graphics_class = "recommended-pill"
            graphics_target = graphics_page_name(updates[0][0])
        elif waiting_for_restart:
            graphics_state = _("Reboot Required")
            graphics_subtitle = waiting_for_restart[0][0].title
            graphics_class = "warning-pill"
            graphics_target = graphics_page_name(waiting_for_restart[0][0])
        elif unhealthy:
            graphics_state = _("Support needs attention")
            graphics_subtitle = unhealthy[0].title
            graphics_class = "warning-pill"
            graphics_target = graphics_page_name(unhealthy[0])
        else:
            graphics_state = _("Ready")
            graphics_subtitle = (
                graphics_scan.devices[0].title
                if graphics_scan.devices
                else _("No additional drivers are needed.")
            )
            graphics_class = "success-pill"
            graphics_target = "graphics-0" if graphics_scan.devices else None
        cards.insert(
            self._overview_card(
                "video-display-symbolic",
                _("Available drivers"),
                graphics_state,
                graphics_subtitle,
                graphics_class,
                graphics_target,
            ),
            -1,
        )

        cards.insert(
            self._overview_card(
                "audio-card-symbolic",
                _("Audio Support"),
                _("Ready") if audio.ready else _("Needs attention"),
                _("Audio support ready") if audio.ready else _("Support needs attention"),
                "success-pill" if audio.ready else "warning-pill",
                "audio",
            ),
            -1,
        )

        printing_ready = (
            printing.service_running
            and not printing.missing_required_packages
            and not printing.disabled_printers
        )
        if not printing.startup_enabled:
            printing_state = _("Disabled")
            printing_subtitle = _("Printing support disabled.")
            printing_class = "installed-pill"
        elif printing_ready:
            printing_state = _("Ready")
            printing_subtitle = (
                gettext.ngettext(
                    "%d printer configured",
                    "%d printers configured",
                    len(printing.printers),
                ) % len(printing.printers)
                if printing.printers
                else _("No printers configured")
            )
            printing_class = "success-pill"
        else:
            printing_state = _("Needs attention")
            printing_subtitle = _("Support needs attention")
            printing_class = "warning-pill"
        cards.insert(
            self._overview_card(
                "printer-symbolic",
                _("Printing Support"),
                printing_state,
                printing_subtitle,
                printing_class,
                "printing",
            ),
            -1,
        )

        xbox_ready = xbox.status in {XboxStatus.LOADED, XboxStatus.READY}
        xbox_optional = xbox.status is XboxStatus.NOT_INSTALLED
        cards.insert(
            self._overview_card(
                "input-gaming-symbolic",
                _("Xbox Controller Support"),
                _("Ready") if xbox_ready else (
                    _("Not installed") if xbox_optional else _("Needs attention")
                ),
                _("xpadneo installed") if xbox_ready else (
                    _("Optional Bluetooth driver") if xbox_optional
                    else _("Support needs attention")
                ),
                "success-pill" if xbox_ready else (
                    "installed-pill" if xbox_optional else "warning-pill"
                ),
                "xbox",
            ),
            -1,
        )

        if not secure_boot.enforcement_inactive:
            cards.insert(
                self._overview_card(
                    "security-high-symbolic",
                    _("Secure Boot"),
                    _("Trusted") if secure_boot.ready else _("Action required"),
                    _("Trust established") if secure_boot.ready else _("Support needs attention"),
                    "success-pill" if secure_boot.ready else "warning-pill",
                    "secure-boot",
                ),
                -1,
            )
        firmware_state, firmware_subtitle, firmware_class = (
            self._firmware_card_state(self._firmware_manager.snapshot)
        )
        cards.insert(
            self._overview_card(
                "application-x-firmware-symbolic",
                _("Device Firmware"),
                firmware_state,
                firmware_subtitle,
                firmware_class,
                "firmware",
            ),
            -1,
        )
        content.append(cards)

        if recommendations:
            group = Adw.PreferencesGroup(title=_("Available drivers"))
            for device, option in recommendations:
                row = Adw.ActionRow(
                    title=device.title,
                    subtitle=version_summary(option),
                )
                icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
                icon.set_pixel_size(24)
                row.add_prefix(icon)
                if not option.installed:
                    state = _("Recommended")
                    state_class = "recommended-pill"
                elif option.update_available:
                    state = _("Update available")
                    state_class = "recommended-pill"
                elif (device, option) in waiting_for_restart:
                    state = _("Reboot Required")
                    state_class = "warning-pill"
                else:
                    state = _("Ready")
                    state_class = "success-pill"
                row.add_suffix(
                    _pill(state, state_class)
                )
                group.add(row)
            content.append(group)

        actions = Adw.PreferencesGroup(title=_("Driver status"))
        refresh_row = Adw.ActionRow(
            title=_("Check for Driver Updates"),
            subtitle=_(
                "Refresh software sources and compare the recommended driver version."
            ),
        )
        refresh_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        refresh_icon.set_pixel_size(24)
        refresh_row.add_prefix(refresh_icon)
        refresh_button = Gtk.Button(label=_("Scan again"), valign=Gtk.Align.CENTER)
        refresh_button.connect(
            "clicked",
            lambda button: self._run_action(
                button,
                ["refresh-driver-info"],
                success_message=_("Driver information updated."),
            ),
        )
        refresh_row.add_suffix(refresh_button)
        actions.add(refresh_row)

        install_row = Adw.ActionRow(
            title="ubuntu-drivers install",
            subtitle=_(
                "AnduinOS will update software sources and install the drivers "
                "recommended for this hardware."
            ),
        )
        install_icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        install_icon.set_pixel_size(24)
        install_row.add_prefix(install_icon)
        if secure_boot.ready:
            install_button = Gtk.Button(
                label=_("Apply Changes"), valign=Gtk.Align.CENTER
            )
            install_button.add_css_class("suggested-action")
            install_button.connect(
                "clicked", self._confirm_recommended_install
            )
        else:
            install_button = Gtk.Button(
                label=_("Secure Boot"), valign=Gtk.Align.CENTER
            )
            install_button.connect(
                "clicked", lambda _button: self._select_page("secure-boot")
            )
        install_row.add_suffix(install_button)
        actions.add(install_row)
        content.append(actions)

        return scroll

    def _overview_card(
        self,
        icon_name: str,
        title: str,
        state: str,
        subtitle: str,
        state_class: str,
        target: str | None,
    ) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_size_request(290, -1)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(28)
        icon.set_valign(Gtk.Align.START)
        icon.add_css_class("accent")
        content.append(icon)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        labels.set_hexpand(True)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label=title, xalign=0, wrap=True)
        heading.add_css_class("heading")
        heading.set_hexpand(True)
        title_row.append(heading)
        title_row.append(_pill(state, state_class))
        labels.append(title_row)
        detail = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        detail.add_css_class("caption")
        detail.add_css_class("dim-label")
        labels.append(detail)
        content.append(labels)

        if target:
            arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
            arrow.add_css_class("dim-label")
            content.append(arrow)
            card = Gtk.Button(has_frame=False)
            card.add_css_class("card")
            card.add_css_class("overview-card")
            card.set_hexpand(True)
            card.set_child(content)
            card.connect("clicked", lambda _button: self._select_page(target))
            return card

        card = Gtk.Box()
        card.add_css_class("card")
        card.set_hexpand(True)
        card.append(content)
        return card

    def _firmware_page(self, state: FirmwareSnapshot) -> Gtk.Widget:
        page, content = self._page_shell(
            _("Device Firmware"),
            _(
                "Keep system firmware and supported hardware up to date through "
                "the Linux Vendor Firmware Service."
            ),
            "firmware.svg",
        )
        self._firmware_progress = None
        self._firmware_progress_label = None
        self._firmware_request_label = None

        if state.error:
            content.append(
                self._warning_banner(
                    _("Firmware operation failed: ") + state.error,
                    _("Check Again"),
                    self._firmware_manager.check_updates,
                )
            )

        if state.busy or state.loading:
            progress_group = Adw.PreferencesGroup(title=_("Firmware operation"))
            progress_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
            )
            progress_box.set_margin_top(12)
            progress_box.set_margin_bottom(12)
            progress_box.set_margin_start(12)
            progress_box.set_margin_end(12)
            self._firmware_progress_label = Gtk.Label(
                label=_("Checking for updates…"), xalign=0
            )
            self._firmware_progress = Gtk.ProgressBar(show_text=True)
            self._firmware_request_label = Gtk.Label(
                xalign=0, wrap=True, visible=False
            )
            self._firmware_request_label.add_css_class("warning")
            progress_box.append(self._firmware_progress_label)
            progress_box.append(self._firmware_progress)
            progress_box.append(self._firmware_request_label)
            progress_row = Adw.PreferencesRow()
            progress_row.set_child(progress_box)
            progress_group.add(progress_row)
            content.append(progress_group)

        overview = Adw.PreferencesGroup(title=_("System status"))
        service_row = Adw.ActionRow(
            title=_("Firmware service"),
            subtitle=(
                f'fwupd {state.daemon_version}'
                if state.connected and state.daemon_version
                else _("Not available")
            ),
        )
        service_row.add_suffix(
            _status_icon(
                "emblem-ok-symbolic" if state.connected else "dialog-warning-symbolic",
                "success" if state.connected else "warning",
            )
        )
        overview.add(service_row)

        checked = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(state.last_refresh))
            if state.last_refresh
            else _("Not refreshed in this session")
        )
        checked_row = Adw.ActionRow(
            title=_("Firmware metadata"), subtitle=checked
        )
        checked_row.add_suffix(
            _status_icon("dialog-information-symbolic", "dim-label")
        )
        overview.add(checked_row)

        device_count = len(state.devices)
        devices_row = Adw.ActionRow(
            title=_("Supported devices"),
            subtitle=gettext.ngettext(
                "%d device detected", "%d devices detected", device_count
            ) % device_count,
        )
        devices_row.add_suffix(
            _status_icon("dialog-information-symbolic", "dim-label")
        )
        overview.add(devices_row)

        update_count = len(state.updates)
        updates_row = Adw.ActionRow(
            title=_("Available firmware updates"),
            subtitle=gettext.ngettext(
                "%d update available", "%d updates available", update_count
            ) % update_count,
        )
        updates_row.add_suffix(
            _pill(
                _("Update available") if update_count else _("Ready"),
                "recommended-pill" if update_count else "success-pill",
            )
        )
        overview.add(updates_row)
        content.append(overview)

        actions = Adw.PreferencesGroup(title=_("Firmware actions"))
        refresh_row = Adw.ActionRow(
            title=_("Refresh Firmware Metadata"),
            subtitle=_("Download the latest metadata from enabled firmware sources."),
        )
        refresh_button = Gtk.Button(
            label=_("Refresh"), valign=Gtk.Align.CENTER
        )
        refresh_button.set_sensitive(state.connected and not state.busy)
        refresh_button.connect(
            "clicked", lambda _button: self._firmware_manager.refresh_metadata()
        )
        refresh_row.add_suffix(refresh_button)
        actions.add(refresh_row)

        check_row = Adw.ActionRow(
            title=_("Check for Firmware Updates"),
            subtitle=_("Compare connected devices with the available metadata."),
        )
        check_button = Gtk.Button(
            label=_("Check Again"), valign=Gtk.Align.CENTER
        )
        check_button.set_sensitive(state.connected and not state.busy)
        check_button.connect(
            "clicked", lambda _button: self._firmware_manager.check_updates()
        )
        check_row.add_suffix(check_button)
        actions.add(check_row)

        if update_count:
            update_all_row = Adw.ActionRow(
                title=_("Update All Firmware"),
                subtitle=gettext.ngettext(
                    "Install %d available update.",
                    "Install all %d available updates.",
                    update_count,
                ) % update_count,
            )
            update_all_button = Gtk.Button(
                label=_("Update All"), valign=Gtk.Align.CENTER
            )
            update_all_button.add_css_class("suggested-action")
            update_all_button.set_sensitive(not state.busy)
            update_all_button.connect(
                "clicked",
                lambda _button: self._confirm_firmware_update(
                    [device.device_id for device in state.updates]
                ),
            )
            update_all_row.add_suffix(update_all_button)
            actions.add(update_all_row)
        content.append(actions)

        devices = Adw.PreferencesGroup(
            title=_("Firmware Devices"),
            description=_(
                "Expand a device to inspect installed and available firmware versions."
            ),
        )
        if state.devices:
            for device in state.devices:
                devices.add(self._firmware_device_row(device, state.busy))
        else:
            devices.add(
                Adw.ActionRow(
                    title=_("No supported firmware devices"),
                    subtitle=_("The firmware service did not report any manageable devices."),
                )
            )
        content.append(devices)

        history = Adw.PreferencesGroup(
            title=_("Firmware Update History"),
            description=_("Results reported by the fwupd service."),
        )
        if state.history:
            for entry in state.history[:20]:
                state_text, state_class = self._firmware_history_state(entry.state)
                timestamp = (
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.timestamp))
                    if entry.timestamp
                    else _("Unknown time")
                )
                details = [timestamp]
                if entry.version:
                    details.append(f'{_("Installed")}: {entry.version}')
                if entry.error:
                    details.append(entry.error)
                row = Adw.ActionRow(
                    title=entry.name or _("Firmware device"),
                    subtitle=" · ".join(details),
                )
                row.add_suffix(_pill(state_text, state_class))
                history.add(row)
        else:
            history.add(
                Adw.ActionRow(
                    title=_("No firmware update history"),
                    subtitle=_("Completed firmware operations will appear here."),
                )
            )
        content.append(history)
        return page

    def _firmware_device_row(
        self, device: FirmwareDevice, busy: bool
    ) -> Adw.ExpanderRow:
        subtitle_parts = []
        if device.vendor:
            subtitle_parts.append(device.vendor)
        subtitle_parts.append(
            f'{_("Installed")}: {device.version or _("Unknown")}'
        )
        row = Adw.ExpanderRow(
            title=device.name or _("Firmware device"),
            subtitle=" · ".join(subtitle_parts),
        )
        icon = Gtk.Image.new_from_icon_name("application-x-firmware-symbolic")
        icon.set_pixel_size(24)
        row.add_prefix(icon)

        current = Adw.ActionRow(
            title=_("Current version"),
            subtitle=device.version or _("Unknown"),
        )
        row.add_row(current)

        if device.release:
            row.add_suffix(_pill(_("Update available"), "recommended-pill"))
            update_button = Gtk.Button(label=_("Update"), valign=Gtk.Align.CENTER)
            update_button.add_css_class("suggested-action")
            update_button.set_sensitive(not busy)
            update_button.connect(
                "clicked",
                lambda _button: self._confirm_firmware_update([device.device_id]),
            )
            row.add_suffix(update_button)
            available = Adw.ActionRow(
                title=_("Available version"),
                subtitle=device.release.version or _("Unknown"),
            )
            row.add_row(available)
            urgency_names = {
                1: _("Low"),
                2: _("Medium"),
                3: _("High"),
                4: _("Critical"),
            }
            urgency = Adw.ActionRow(
                title=_("Update urgency"),
                subtitle=urgency_names.get(device.release.urgency, _("Unknown")),
            )
            row.add_row(urgency)
            release_notes = []
            for notes in (device.release.summary, device.release.description):
                if notes and notes not in release_notes:
                    release_notes.append(notes)
            for notes in release_notes:
                row.add_row(
                    Adw.ActionRow(
                        title=_("Release notes"), subtitle=notes
                    )
                )
        elif device.update_error:
            row.add_suffix(_pill(_("Needs attention"), "warning-pill"))
            row.add_row(
                Adw.ActionRow(
                    title=_("Firmware status"), subtitle=device.update_error
                )
            )
        else:
            row.add_suffix(_pill(_("Up to date"), "success-pill"))

        requirements = []
        if device.require_ac:
            requirements.append(_("Connect the computer to AC power"))
        if device.needs_shutdown:
            requirements.append(_("Shutdown required after installation"))
        elif device.needs_reboot:
            requirements.append(_("Restart required after installation"))
        if requirements:
            row.add_row(
                Adw.ActionRow(
                    title=_("Installation requirements"),
                    subtitle=" · ".join(requirements),
                )
            )
        return row

    @staticmethod
    def _firmware_history_state(state: int) -> tuple[str, str]:
        return {
            1: (_("Pending"), "installed-pill"),
            2: (_("Success"), "success-pill"),
            3: (_("Failed"), "warning-pill"),
            4: (_("Reboot Required"), "warning-pill"),
            5: (_("Failed"), "warning-pill"),
        }.get(state, (_("Unknown"), "installed-pill"))

    def _confirm_firmware_update(self, device_ids: list[str]) -> None:
        selected = [
            device
            for device in self._firmware_manager.snapshot.updates
            if device.device_id in device_ids
        ]
        if not selected:
            return
        if len(selected) == 1:
            device = selected[0]
            body = _(
                "Install firmware for %(device)s from %(current)s to %(available)s?"
            ) % {
                "device": device.name or _("Firmware device"),
                "current": device.version or _("Unknown"),
                "available": device.release.version if device.release else _("Unknown"),
            }
        else:
            body = gettext.ngettext(
                "Install %d available firmware update?",
                "Install all %d available firmware updates?",
                len(selected),
            ) % len(selected)
        requirements = []
        if any(device.require_ac for device in selected):
            requirements.append(_("Keep the computer connected to AC power."))
        if any(device.affects_fde for device in selected):
            requirements.append(
                _(
                    "This update may invalidate full-disk encryption secrets. Make "
                    "sure you have the volume recovery key before continuing."
                )
            )
        if any(device.needs_reboot or device.needs_shutdown for device in selected):
            requirements.append(_("A restart may be required to finish installation."))
        requirements.append(_("Do not disconnect devices or turn off the computer."))
        body = body + "\n\n" + "\n".join(requirements)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Install Firmware Update"),
            body=body,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("update", _("Update"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("update")
        dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _dialog, response: self._firmware_manager.install(device_ids)
            if response == "update"
            else None,
        )
        dialog.present()

    def _confirm_recommended_install(self, button: Gtk.Button) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Apply Changes"),
            body=_(
                "AnduinOS will update software sources and install the drivers "
                "recommended for this hardware."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Apply"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _dialog, response: self._run_action(
                button,
                ["install-recommended"],
                success_output_marker="+ ubuntu-drivers install",
            ) if response == "install" else None,
        )
        dialog.present()

    def _graphics_page(self, device: HardwareDevice, secure_boot: SecureBootState) -> Gtk.Widget:
        scroll, content = self._page_shell(
            device.title,
            _("Choose the driver used by this device. AnduinOS marks the hardware-tested recommendation."),
            "nvidia.svg" if "nvidia" in device.vendor.lower() else None,
        )
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.set_vexpand(True)
        page.append(scroll)
        group = Adw.PreferencesGroup(title=_("Available drivers"))
        content.append(group)
        if not secure_boot.ready:
            warning = self._warning_banner(
                _(
                    "Secure Boot status or trust must be resolved before installing a third-party driver."
                ),
                _("Secure Boot"),
                lambda: self._select_page("secure-boot"),
            )
            content.append(warning)
        selection: dict[str, str | None] = {"package": None}
        active_package = next(
            (option.package for option in device.options if option.active), None
        )
        button = Gtk.Button(label=_("Apply Changes"))
        button.add_css_class("suggested-action")
        button.set_sensitive(False)

        first_check: Gtk.CheckButton | None = None

        def build_row(option) -> Adw.ActionRow:
            nonlocal first_check
            traits = []
            traits.append(_("open source") if option.free else _("proprietary"))
            if option.builtin:
                traits.append(_("built in"))
            row = Adw.ActionRow(title=option.package, subtitle=" · ".join(traits))
            check = Gtk.CheckButton()
            if first_check:
                check.set_group(first_check)
            else:
                first_check = check
            check.connect(
                "toggled",
                self._driver_selected,
                selection,
                option.package,
                active_package,
                secure_boot.ready,
                button,
            )
            if option.active or (
                active_package is None
                and selection["package"] is None
                and option.recommended
            ):
                check.set_active(True)
            row.add_prefix(check)
            if option.active:
                row.add_suffix(_pill(_("In use"), "in-use-pill"))
            else:
                if option.installed:
                    row.add_suffix(_pill(_("Installed"), "installed-pill"))
                if option.recommended:
                    row.add_suffix(_pill(_("Recommended"), "recommended-pill"))
            return row

        primary = [
            option for option in device.options
            if option.installed or option.recommended or option.builtin
        ]
        advanced = [option for option in device.options if option not in primary]
        primary.sort(
            key=lambda option: (
                not option.active,
                not option.installed,
                not option.recommended,
                option.package,
            )
        )
        advanced.sort(key=lambda option: option.package, reverse=True)
        for option in primary:
            group.add(build_row(option))

        if not device.driver_state_known:
            warning = self._warning_banner(
                f'{_("Kernel module")}: {_("Not detected")}',
                _("Scan again"),
                self.refresh,
            )
            content.append(warning)
        elif (
            device.active_driver
            and device.active_driver.lower().replace("_", "-").startswith("nvidia")
            and device.active_driver_healthy is False
        ):
            warning = self._warning_banner(
                f'{_("Kernel module")}: nvidia · '
                f'{_("Driver operation failed: ")}'
                f'{device.active_driver_error or "nvidia-smi"}',
                _("Repair & Reinstall") if active_package else _("Apply Changes"),
                (
                    lambda: self._run_action(
                        button, ["repair-nvidia", active_package]
                    )
                    if active_package
                    else lambda: button.emit("clicked")
                ),
            )
            content.append(warning)
        elif active_package is None:
            warning = self._warning_banner(
                f'{_("Kernel module")}: {device.active_driver or _("Not detected")}',
                _("Apply Changes"),
                lambda: button.emit("clicked"),
            )
            content.append(warning)

        if advanced:
            advanced_group = Adw.PreferencesGroup()
            advanced_row = Adw.ExpanderRow(
                title=_("Advanced driver versions"),
                subtitle=_("Older, newer, and server-oriented packages"),
            )
            for option in advanced:
                advanced_row.add_row(build_row(option))
            advanced_group.add(advanced_row)
            content.append(advanced_group)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        footer.add_css_class("driver-footer")
        footer.set_halign(Gtk.Align.FILL)
        footer.set_margin_top(0)
        footer.set_margin_bottom(0)
        footer.set_margin_start(0)
        footer.set_margin_end(0)
        footer.set_size_request(-1, 68)
        footer_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        footer_content.set_hexpand(True)
        footer_content.set_halign(Gtk.Align.FILL)
        footer_content.set_valign(Gtk.Align.CENTER)
        footer_content.set_margin_start(24)
        footer_content.set_margin_end(24)
        status = Gtk.Label(label=_("Select another driver to apply changes."), xalign=0)
        status.add_css_class("dim-label")
        status.set_hexpand(True)
        footer_content.append(status)
        footer_content.append(button)
        footer.append(footer_content)
        page.append(footer)

        button.connect(
            "clicked",
            lambda btn: self._run_action(
                btn,
                ["install", selection["package"]]
                if selection["package"] else [],
            ),
        )
        return page

    def _driver_selected(
        self,
        radio: Gtk.CheckButton,
        selection: dict[str, str | None],
        package: str,
        active_package: str | None,
        secure_boot_ready: bool,
        apply_button: Gtk.Button,
    ) -> None:
        if radio.get_active():
            selection["package"] = package
            apply_button.set_sensitive(
                secure_boot_ready and package != active_package
            )

    def _xbox_page(self, state: XboxState, secure_boot: SecureBootState) -> Gtk.Widget:
        page, content = self._page_shell(
            _("Xbox Controller Support"),
            _("xpadneo improves Bluetooth mapping, rumble, battery reporting and compatibility for modern Xbox controllers."),
            "input-gaming.svg",
        )
        group = Adw.PreferencesGroup(title=_("Driver status"))
        content.append(group)
        self._add_state_row(
            group,
            _("Driver package"),
            _("Installed") if state.installed else _("Not installed"),
            state.installed,
            _("Install Driver") if not state.installed else None,
            (lambda button: self._run_action(button, ["install-xbox"]))
            if not state.installed else None,
        )
        if not secure_boot.enforcement_inactive:
            signature_good: bool | None = True
            signature_text = _("Trusted")
            signature_action = None
            signature_action_label = None
            if state.status in {
                XboxStatus.NOT_INSTALLED,
                XboxStatus.MODULE_MISSING,
            }:
                signature_good = None
                signature_text = _("Not detected")
            elif state.status is XboxStatus.SECURE_BOOT_UNKNOWN:
                signature_good = False
                signature_text = _("Secure Boot state could not be determined")
                signature_action_label = _("Secure Boot")
                signature_action = lambda _button: self._select_page("secure-boot")
            elif state.status is XboxStatus.ENROLLMENT_PENDING:
                signature_good = False
                signature_text = _("Pending enrollment in blue screen (MOKManager)")
                signature_action_label = _("Secure Boot")
                signature_action = lambda _button: self._select_page("secure-boot")
            elif state.status is XboxStatus.TRUST_SETUP_REQUIRED:
                signature_good = False
                signature_text = _("Certificate is not trusted by motherboard")
                signature_action_label = _("Secure Boot")
                signature_action = lambda _button: self._select_page("secure-boot")
            elif state.status is XboxStatus.SIGNATURE_MISMATCH:
                signature_good = False
                signature_text = _("Some DKMS modules need to be re-signed")
                if secure_boot.configuration_present:
                    signature_action_label = _("Repair & Reinstall")
                    signature_action = lambda button: self._run_action(
                        button, ["repair-xbox"]
                    )
                else:
                    signature_action_label = _("Secure Boot")
                    signature_action = lambda _button: self._select_page("secure-boot")
            self._add_state_row(
                group,
                _("Module signature"),
                signature_text,
                signature_good,
                signature_action_label,
                signature_action,
            )

        if state.status is XboxStatus.MODULE_MISSING:
            module_text = _("Missing")
            module_good: bool | None = False
            module_action_label = _("Repair & Reinstall")
            module_action = lambda button: self._run_action(
                button, ["repair-xbox"]
            )
        elif state.status is XboxStatus.LOAD_STATE_UNKNOWN:
            module_text = _("Not detected")
            module_good = False
            module_action_label = _("Scan again")
            module_action = lambda _button: self.refresh()
        elif state.status is XboxStatus.LOADED:
            module_text = _("Loaded")
            module_good = True
            module_action_label = None
            module_action = None
        elif state.module_available:
            module_text = _("Standing by")
            module_good = (
                None
                if state.status in {
                    XboxStatus.SECURE_BOOT_UNKNOWN,
                    XboxStatus.ENROLLMENT_PENDING,
                    XboxStatus.TRUST_SETUP_REQUIRED,
                    XboxStatus.SIGNATURE_MISMATCH,
                }
                else True
            )
            module_action_label = None
            module_action = None
        else:
            module_text = _("Not installed")
            module_good = None
            module_action_label = None
            module_action = None
        self._add_state_row(
            group,
            _("Kernel module"),
            module_text,
            module_good,
            module_action_label,
            module_action,
        )
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.END)
        bluetooth = Gtk.Button(label=_("Bluetooth Settings"))
        bluetooth.connect("clicked", lambda _b: subprocess.Popen(["gnome-control-center", "bluetooth"]))
        actions.append(bluetooth)
        content.append(actions)
        return page

    def _audio_page(self, state: AudioState) -> Gtk.Widget:
        page, content = self._page_shell(
            _("Audio Support"),
            _("AnduinOS provides Intel SOF firmware and ALSA UCM profiles for reliable audio initialization and routing."),
            "audio.svg",
        )
        packages = Adw.PreferencesGroup(title=_("Support packages"))
        content.append(packages)
        audio_action = (
            ["install-audio"]
            if not state.packages_installed
            else ["repair-audio"]
        )
        audio_action_label = (
            _("Install Audio Support")
            if not state.packages_installed
            else _("Repair & Reinstall")
        )

        def repair_audio(button: Gtk.Button) -> None:
            self._run_action(button, audio_action)

        self._add_state_row(
            packages,
            _("Intel SOF firmware"),
            state.sof_package.version if state.sof_package.installed else _("Not installed"),
            state.sof_package.installed,
            audio_action_label if not state.sof_package.installed else None,
            repair_audio if not state.sof_package.installed else None,
        )
        self._add_state_row(
            packages,
            _("ALSA UCM profiles"),
            state.ucm_package.version if state.ucm_package.installed else _("Not installed"),
            state.ucm_package.installed,
            audio_action_label if not state.ucm_package.installed else None,
            repair_audio if not state.ucm_package.installed else None,
        )

        runtime = Adw.PreferencesGroup(title=_("Runtime status"))
        content.append(runtime)
        self._add_state_row(
            runtime,
            _("SOF firmware files"),
            _("Available") if state.firmware_present else _("Missing"),
            state.firmware_present,
            _("Repair & Reinstall") if not state.firmware_present else None,
            repair_audio if not state.firmware_present else None,
        )
        self._add_state_row(
            runtime,
            _("UCM configuration files"),
            _("Available") if state.ucm_profiles_present else _("Missing"),
            state.ucm_profiles_present,
            _("Repair & Reinstall") if not state.ucm_profiles_present else None,
            repair_audio if not state.ucm_profiles_present else None,
        )
        self._add_state_row(
            runtime,
            _("SOF kernel modules"),
            ", ".join(state.sof_modules) if state.sof_modules else _("Not currently loaded"),
            True if state.sof_modules else None,
        )
        self._add_state_row(
            runtime,
            _("Active audio drivers"),
            ", ".join(state.active_drivers) if state.active_drivers else _("Not detected"),
            True if state.active_drivers else None,
        )

        return page

    def _printing_page(self, state: PrintingState) -> Gtk.Widget:
        page, content = self._page_shell(
            _("Printing Support"),
            _("Inspect the local print service, configured queues, and the packages that provide modern and legacy printer support."),
            "printer.svg",
        )
        availability = Adw.PreferencesGroup()
        enable_printing = Adw.SwitchRow(
            title=_("Enable Printing Support"),
            subtitle=_("Allow local, network, and USB printing services to run."),
        )
        enable_printing.set_active(state.startup_enabled)
        enable_printing.connect(
            "notify::active", self._printing_switch_changed
        )
        availability.add(enable_printing)
        content.append(availability)

        if state.service_running:
            printer_actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
                halign=Gtk.Align.END,
            )
            add_printer = Gtk.Button(label=_("Add Printer"))
            add_printer.add_css_class("suggested-action")
            add_printer.connect(
                "clicked",
                lambda _button: subprocess.Popen(
                    ["gnome-control-center", "printers"]
                ),
            )
            printer_actions.append(add_printer)
            content.append(printer_actions)

        overview = Adw.PreferencesGroup(title=_("System status"))
        content.append(overview)
        self._add_state_row(
            overview,
            _("CUPS service"),
            _("Running") if state.service_running else _("Stopped"),
            state.service_running if state.startup_enabled else None,
            _("Enable Printing Support")
            if state.startup_enabled and not state.service_running else None,
            (
                lambda button: self._run_action(
                    button, ["set-printing-enabled", "true"]
                )
            ) if state.startup_enabled and not state.service_running else None,
        )
        self._add_state_row(
            overview,
            _("Start at boot"),
            _("Enabled") if state.startup_enabled else _("Disabled"),
            True if state.startup_enabled else None,
        )
        printer_count = len(state.printers)
        printer_summary = gettext.ngettext(
            "%d configured printer",
            "%d configured printers",
            printer_count,
        ) % printer_count
        self._add_state_row(
            overview,
            _("Configured printers"),
            printer_summary,
            True if printer_count else None,
        )
        self._add_state_row(
            overview,
            _("Default printer"),
            state.default_printer or _("Not set"),
            True if state.default_printer else None,
        )
        if not state.printers:
            queue_summary = _("No configured queues")
            queue_good = None
        elif state.disabled_printers:
            paused = len(state.disabled_printers)
            queue_summary = gettext.ngettext(
                "%d queue paused", "%d queues paused", paused
            ) % paused
            queue_good = False
        else:
            queue_summary = _("All queues enabled")
            queue_good = True
        self._add_state_row(
            overview,
            _("Print queues"),
            queue_summary,
            queue_good,
            _("Apply Changes") if queue_good is False else None,
            (
                lambda button: self._run_action(button, ["resume-print-queues"])
            ) if queue_good is False else None,
        )

        content.append(
            self._printing_package_group(
                _("Core printing"),
                _("Required for the local print service and command-line clients."),
                state.core_packages,
                required=True,
            )
        )
        content.append(
            self._printing_package_group(
                _("Driverless printing"),
                _("Modern IPP drivers, document filters, and capability tools."),
                state.driverless_packages,
                required=True,
            )
        )
        content.append(
            self._printing_package_group(
                _("Network discovery"),
                _("Automatic discovery of printers advertised on the local network."),
                state.discovery_packages,
                required=False,
            )
        )
        content.append(
            self._printing_package_group(
                _("Optional compatibility"),
                _("USB IPP, administrative authorization, legacy drivers, and network scanning."),
                state.optional_packages,
                required=False,
            )
        )
        return page

    def _printing_package_group(
        self,
        title: str,
        description: str,
        packages: tuple[PackageState, ...],
        required: bool,
    ) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title, description=description)
        for package in packages:
            self._add_state_row(
                group,
                package.name,
                package.version if package.installed else _("Not installed"),
                package.installed if required else (
                    True if package.installed else None
                ),
                _("Install Missing Packages")
                if required and not package.installed else None,
                (
                    lambda button: self._run_action(
                        button, ["install-printing-support"]
                    )
                ) if required and not package.installed else None,
            )
        return group

    def _printing_switch_changed(
        self, row: Adw.SwitchRow, _parameter
    ) -> None:
        row.set_sensitive(False)
        enabled = row.get_active()

        def worker() -> None:
            try:
                result = subprocess.run(
                    [
                        "pkexec",
                        HELPER,
                        "set-printing-enabled",
                        "true" if enabled else "false",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=1800,
                    check=False,
                )
                message = (
                    result.stdout.strip().splitlines()[-1]
                    if result.stdout.strip()
                    else result.stderr.strip()
                )
                GLib.idle_add(
                    self._printing_switch_done,
                    enabled,
                    result.returncode,
                    message,
                )
            except Exception as error:
                GLib.idle_add(
                    self._printing_switch_done, enabled, 1, str(error)
                )

        threading.Thread(target=worker, daemon=True).start()

    def _printing_switch_done(
        self, enabled: bool, code: int, message: str
    ) -> bool:
        if code == 0:
            self._toast(
                _("Printing support enabled.")
                if enabled
                else _("Printing support disabled.")
            )
        else:
            self._toast(
                _("Printing operation failed: ")
                + (message or _("unknown error"))
            )
        # Re-scan even after failure so the switch always reflects systemd,
        # rather than the optimistic state selected before authentication.
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _secure_boot_page(self, state: SecureBootState, dkms: DkmsState) -> Gtk.Widget:
        initial_state_applied = False

        def secure_boot_state_changed() -> None:
            nonlocal initial_state_applied
            if not initial_state_applied:
                initial_state_applied = True
                return
            GLib.idle_add(self.refresh)

        def icon_factory(name: str) -> Gtk.Image:
            image = Gtk.Image()
            path = _resource_path(name)
            if path.is_file():
                image.set_from_file(str(path))
            else:
                image.set_from_icon_name(name)
            return image

        secure_boot_page = create_secure_boot_page(
            translate=_,
            icon_factory=icon_factory,
            state_changed=secure_boot_state_changed,
            initial_state=(state, dkms),
        )
        secure_boot_page.set_valign(Gtk.Align.START)
        secure_boot_page.set_vexpand(False)
        scroll = _scrolled_window()
        clamp = Adw.Clamp(maximum_size=650, tightening_threshold=500)
        clamp.set_child(secure_boot_page)
        scroll.set_child(clamp)
        return scroll

    def _add_state_row(
        self,
        group: Adw.PreferencesGroup,
        title: str,
        subtitle: str,
        good: bool | None,
        action_label: str | None = None,
        action: Callable[[Gtk.Button], None] | None = None,
    ) -> None:
        if good is False and (not action_label or action is None):
            raise ValueError(f"Warning row has no recovery action: {title}")
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if good is None:
            row.add_suffix(_status_icon("dialog-information-symbolic", "dim-label"))
        else:
            row.add_suffix(_status_icon("emblem-ok-symbolic" if good else "dialog-warning-symbolic", "success" if good else "warning"))
        if action_label and action:
            button = Gtk.Button(label=action_label, valign=Gtk.Align.CENTER)
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda clicked: action(clicked))
            row.add_suffix(button)
        group.add(row)

    def _warning_banner(
        self,
        title: str,
        action_label: str,
        action: Callable[[], None],
    ) -> Adw.Banner:
        if not action_label:
            raise ValueError(f"Warning banner has no recovery action: {title}")
        banner = Adw.Banner(title=title)
        banner.set_button_label(action_label)
        banner.connect("button-clicked", lambda _banner: action())
        banner.set_revealed(True)
        return banner

    def _run_action(
        self,
        button: Gtk.Button,
        arguments: list[str],
        stdin: str | None = None,
        success_message: str | None = None,
        success_output_marker: str | None = None,
    ) -> None:
        if not arguments: return
        button.set_sensitive(False)
        original = button.get_label() or _("Apply")
        button.set_label(_("Working…"))
        def worker() -> None:
            try:
                result = subprocess.run(["pkexec", HELPER, *arguments], input=stdin, capture_output=True, text=True, timeout=1800, check=False)
                message = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr.strip()
                resolved_success_message = success_message
                if result.returncode == 0 and success_output_marker:
                    resolved_success_message = _command_output_summary(
                        result.stdout, success_output_marker
                    ) or success_message
                GLib.idle_add(
                    self._action_done,
                    button,
                    original,
                    result.returncode,
                    message,
                    resolved_success_message,
                )
            except Exception as error:
                GLib.idle_add(
                    self._action_done,
                    button,
                    original,
                    1,
                    str(error),
                    success_message,
                )
        threading.Thread(target=worker, daemon=True).start()

    def _action_done(
        self,
        button: Gtk.Button,
        original: str,
        code: int,
        message: str,
        success_message: str | None = None,
    ) -> bool:
        button.set_label(original); button.set_sensitive(True)
        self._toast(
            (success_message or _("Driver changes completed. Restart may be required."))
            if code == 0
            else (_("Driver operation failed: ") + (message or _("unknown error")))
        )
        if code == 0: self.refresh()
        return GLib.SOURCE_REMOVE

    def _toast(self, message: str) -> None:
        # A transient alert works on every supported libadwaita, including Noble.
        dialog = Adw.MessageDialog(transient_for=self, heading=message)
        dialog.add_response("ok", _("OK")); dialog.present()


class DriverCenterApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._show_about)
        self.add_action(about_action)

    def _show_about(self, _action: Gio.SimpleAction, _parameter) -> None:
        dialog = Adw.AboutDialog()
        dialog.set_application_name(_("AnduinOS Driver Center"))
        dialog.set_application_icon(APP_ID)
        dialog.set_developer_name(_("AnduinOS Team"))
        dialog.set_version("2.0.2")
        dialog.set_comments(
            _("Install, inspect, and repair hardware drivers on AnduinOS.")
        )
        dialog.set_website("https://www.anduinos.com")
        dialog.set_issue_url(
            "https://github.com/AiursoftWeb/AnduinOS-Packages/issues"
        )
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_copyright("© 2026 AnduinOS Team")
        dialog.present(self.get_active_window())

    def do_activate(self) -> None:
        window = self.get_active_window() or DriverCenterWindow(self)
        window.present()


def main() -> int:
    Adw.init()
    return DriverCenterApplication().run(None)
