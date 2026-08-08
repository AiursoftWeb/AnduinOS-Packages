"""GTK4/libadwaita frontend for AnduinOS Driver Center."""

from __future__ import annotations

import gettext
import os
from pathlib import Path
import subprocess
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .core import AudioState, DkmsState, HardwareDevice, PackageState, PrintingState, SecureBootState, XboxState, XboxStatus, scan_system

try:
    from anduinos_secureboot.ui import create_secure_boot_page
except ModuleNotFoundError:
    import sys
    _toolkit_src = Path(__file__).resolve().parents[3] / "anduinos-secureboot-toolkit" / "src"
    sys.path.insert(0, str(_toolkit_src))
    from anduinos_secureboot.ui import create_secure_boot_page


APP_ID = "com.anduinos.DriverCenter"
HELPER = "/usr/libexec/anduinos-driver-center/driver-helper"
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


class DriverCenterWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=_("AnduinOS Driver Center"))
        self.set_default_size(900, 620)
        self.set_size_request(720, 500)
        self._graphics: list[HardwareDevice] = []
        self._secure_boot: SecureBootState | None = None
        self._xbox: XboxState | None = None
        self._dkms: DkmsState | None = None
        self._audio: AudioState | None = None
        self._printing: PrintingState | None = None
        self._selected_package: str | None = None
        self._selected_page_name: str | None = None
        self._rebuilding_navigation = False

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

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Scan again"))
        self.refresh_button.connect("clicked", lambda _button: self.refresh())
        header.pack_end(self.refresh_button)
        menu = Gio.Menu()
        menu.append(_("About Driver Center"), "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_tooltip_text(_("Main Menu"))
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)
        toolbar.add_top_bar(header)

        self.split = Adw.OverlaySplitView()
        self.split.set_min_sidebar_width(260)
        self.split.set_max_sidebar_width(330)
        self.split.set_sidebar_width_fraction(0.32)
        toolbar.set_content(self.split)
        self.set_content(toolbar)

        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.sidebar.set_margin_top(18)
        self.sidebar.set_margin_bottom(18)
        self.sidebar.set_margin_start(12)
        self.sidebar.set_margin_end(12)
        title = Gtk.Label(label=_("Hardware"), xalign=0)
        title.add_css_class("title-3")
        self.sidebar.append(title)
        self.device_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.device_list.add_css_class("navigation-list")
        self.device_list.connect("row-selected", self._row_selected)
        self.sidebar.append(self.device_list)
        self.split.set_sidebar(self.sidebar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.split.set_content(self.stack)
        self._show_loading()
        self.refresh()

    def _clear(self, widget: Gtk.Widget) -> None:
        child = widget.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            widget.remove(child)
            child = next_child

    def _show_loading(self) -> None:
        self._clear(self.stack)
        status = Adw.StatusPage(title=_("Scanning for drivers"), description=_("Checking hardware and Secure Boot status…"))
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(48, 48)
        status.set_child(spinner)
        self.stack.add_named(status, "loading")
        self.stack.set_visible_child_name("loading")

    def refresh(self) -> None:
        self.refresh_button.set_sensitive(False)
        self._rebuilding_navigation = True
        self._show_loading()

        def worker() -> None:
            result = scan_system()
            GLib.idle_add(self._apply_scan, *result)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_scan(self, graphics: list[HardwareDevice], secure_boot: SecureBootState, xbox: XboxState, dkms: DkmsState, audio: AudioState, printing: PrintingState) -> bool:
        self._graphics, self._secure_boot, self._xbox, self._dkms, self._audio, self._printing = graphics, secure_boot, xbox, dkms, audio, printing
        self.refresh_button.set_sensitive(True)
        self._clear(self.device_list)
        self._clear(self.stack)

        for index, device in enumerate(graphics):
            label = device.title
            subtitle = device.vendor
            row = self._device_row("video-display-symbolic", label, subtitle)
            row.page_name = f"graphics-{index}"
            self.device_list.append(row)
            self.stack.add_named(self._graphics_page(device, secure_boot), row.page_name)

        audio_row = self._device_row(
            "audio-card-symbolic", _("Audio"),
            _("Audio support ready") if audio.ready else _("Support needs attention"),
        )
        audio_row.page_name = "audio"
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
            self.device_list.append(secure_row)
            self.stack.add_named(self._secure_boot_page(secure_boot, dkms), "secure-boot")

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
        return row

    def _row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._rebuilding_navigation:
            return
        if row and hasattr(row, "page_name"):
            self._selected_page_name = row.page_name
            self.stack.set_visible_child_name(row.page_name)

    def _select_page(self, page_name: str) -> None:
        row = self.device_list.get_row_at_index(0)
        while row:
            if getattr(row, "page_name", None) == page_name:
                self.device_list.select_row(row)
                return
            row = self.device_list.get_row_at_index(row.get_index() + 1)

    def _page_shell(
        self, title: str, description: str, illustration: str | None = None
    ) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
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

        return create_secure_boot_page(
            translate=_,
            icon_factory=icon_factory,
            state_changed=secure_boot_state_changed,
            initial_state=(state, dkms),
        )

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

    def _run_action(self, button: Gtk.Button, arguments: list[str], stdin: str | None = None) -> None:
        if not arguments: return
        button.set_sensitive(False)
        original = button.get_label() or _("Apply")
        button.set_label(_("Working…"))
        def worker() -> None:
            try:
                result = subprocess.run(["pkexec", HELPER, *arguments], input=stdin, capture_output=True, text=True, timeout=1800, check=False)
                message = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr.strip()
                GLib.idle_add(self._action_done, button, original, result.returncode, message)
            except Exception as error:
                GLib.idle_add(self._action_done, button, original, 1, str(error))
        threading.Thread(target=worker, daemon=True).start()

    def _action_done(self, button: Gtk.Button, original: str, code: int, message: str) -> bool:
        button.set_label(original); button.set_sensitive(True)
        self._toast(_("Driver changes completed. Restart may be required.") if code == 0 else (_("Driver operation failed: ") + (message or _("unknown error"))))
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
        dialog.set_version("2.0.0")
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
