"""GTK4/libadwaita frontend for AnduinOS Driver Center."""

from __future__ import annotations

import gettext
import os
from pathlib import Path
import subprocess
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .core import AudioState, DkmsState, HardwareDevice, PackageState, PrintingState, SecureBootState, XboxState, scan_system


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
            printing_subtitle = _("Printing service stopped")
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
            _("xpadneo installed") if xbox.installed else _("Optional Bluetooth driver"),
        )
        xbox_row.page_name = "xbox"
        self.device_list.append(xbox_row)
        self.stack.add_named(self._xbox_page(xbox, secure_boot), "xbox")

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
        if secure_boot.enabled and not secure_boot.ready:
            warning = Adw.Banner(title=_("Prepare Secure Boot before installing a third-party driver."))
            warning.set_revealed(True)
            content.append(warning)
        selection: dict[str, str | None] = {"package": None}
        installed_package = next(
            (option.package for option in device.options if option.installed), None
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
            if option.installed or (selection["package"] is None and option.recommended):
                check.set_active(True)
                selection["package"] = option.package
            check.connect(
                "toggled",
                self._driver_selected,
                selection,
                option.package,
                installed_package,
                secure_boot.ready,
                button,
            )
            row.add_prefix(check)
            if option.installed:
                row.add_suffix(_pill(_("In use"), "in-use-pill"))
            elif option.recommended:
                row.add_suffix(_pill(_("Recommended"), "recommended-pill"))
            return row

        primary = [
            option for option in device.options
            if option.installed or option.recommended or option.builtin
        ]
        advanced = [option for option in device.options if option not in primary]
        primary.sort(key=lambda option: (not option.installed, not option.recommended, option.package))
        advanced.sort(key=lambda option: option.package, reverse=True)
        for option in primary:
            group.add(build_row(option))

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
        installed_package: str | None,
        secure_boot_ready: bool,
        apply_button: Gtk.Button,
    ) -> None:
        if radio.get_active():
            selection["package"] = package
            apply_button.set_sensitive(
                secure_boot_ready and package != installed_package
            )

    def _xbox_page(self, state: XboxState, secure_boot: SecureBootState) -> Gtk.Widget:
        page, content = self._page_shell(
            _("Xbox Controller Support"),
            _("xpadneo improves Bluetooth mapping, rumble, battery reporting and compatibility for modern Xbox controllers."),
            "input-gaming.svg",
        )
        group = Adw.PreferencesGroup(title=_("Driver status"))
        content.append(group)
        self._add_state_row(group, _("Driver package"), _("Installed") if state.installed else _("Not installed"), state.installed)
        if secure_boot.enabled:
            self._add_state_row(group, _("Module signature"), _("Trusted") if state.signature_matches and secure_boot.enrolled else _("Needs attention"), state.signature_matches and secure_boot.enrolled)
        self._add_state_row(group, _("Kernel module"), _("Loaded") if state.module_loaded else (_("Blocked by Secure Boot") if state.blocked_by_secure_boot else _("Standing by")), not state.blocked_by_secure_boot)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.END)
        bluetooth = Gtk.Button(label=_("Bluetooth Settings"))
        bluetooth.connect("clicked", lambda _b: subprocess.Popen(["gnome-control-center", "bluetooth"]))
        actions.append(bluetooth)
        if not state.installed:
            install = Gtk.Button(label=_("Install Driver")); install.add_css_class("suggested-action")
            install.set_sensitive(secure_boot.ready)
            install.connect("clicked", lambda btn: self._run_action(btn, ["install-xbox"]))
            actions.append(install)
        elif state.blocked_by_secure_boot or (secure_boot.enabled and not state.signature_matches):
            repair = Gtk.Button(label=_("Repair & Reinstall")); repair.add_css_class("suggested-action")
            repair.set_sensitive(secure_boot.ready)
            repair.connect("clicked", lambda btn: self._run_action(btn, ["repair-xbox"]))
            actions.append(repair)
        content.append(actions)
        return page

    def _audio_page(self, state: AudioState) -> Gtk.Widget:
        page, content = self._page_shell(
            _("Audio Support"),
            _("AnduinOS provides Intel SOF firmware and ALSA UCM profiles for reliable audio initialization and routing."),
        )
        packages = Adw.PreferencesGroup(title=_("Support packages"))
        content.append(packages)
        self._add_state_row(
            packages,
            _("Intel SOF firmware"),
            state.sof_package.version if state.sof_package.installed else _("Not installed"),
            state.sof_package.installed,
        )
        self._add_state_row(
            packages,
            _("ALSA UCM profiles"),
            state.ucm_package.version if state.ucm_package.installed else _("Not installed"),
            state.ucm_package.installed,
        )

        runtime = Adw.PreferencesGroup(title=_("Runtime status"))
        content.append(runtime)
        self._add_state_row(
            runtime,
            _("SOF firmware files"),
            _("Available") if state.firmware_present else _("Missing"),
            state.firmware_present,
        )
        self._add_state_row(
            runtime,
            _("UCM configuration files"),
            _("Available") if state.ucm_profiles_present else _("Missing"),
            state.ucm_profiles_present,
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

        if not state.packages_installed:
            button = Gtk.Button(label=_("Install Audio Support"), halign=Gtk.Align.END)
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda btn: self._run_action(btn, ["install-audio"]))
            content.append(button)
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
            state.service_running,
        )
        self._add_state_row(
            overview,
            _("Start at boot"),
            _("Enabled") if state.startup_enabled else _("Disabled"),
            state.startup_enabled,
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
            overview, _("Print queues"), queue_summary, queue_good
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
        if state.missing_packages:
            missing = len(state.missing_packages)
            action_group = Adw.PreferencesGroup(title=_("Complete printing support"))
            action_row = Adw.ActionRow(
                title=_("Install missing printing packages"),
                subtitle=gettext.ngettext(
                    "%d package is missing",
                    "%d packages are missing",
                    missing,
                ) % missing,
            )
            install = Gtk.Button(
                label=_("Install Missing Packages"),
                valign=Gtk.Align.CENTER,
            )
            install.add_css_class("suggested-action")
            install.connect(
                "clicked",
                lambda btn: self._run_action(
                    btn, ["install-printing-support"]
                ),
            )
            action_row.add_suffix(install)
            action_group.add(action_row)
            content.append(action_group)
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
        page, content = self._page_shell(
            _("Secure Boot Trust"),
            _("AnduinOS signs third-party DKMS modules with a local Machine Owner Key so they can load without disabling Secure Boot."),
            "secureboot-chip.svg",
        )
        group = Adw.PreferencesGroup(title=_("Trust chain"))
        content.append(group)
        self._add_state_row(group, _("Secure Boot"), _("Enabled") if state.enabled else _("Disabled"), True)
        if state.enabled:
            self._add_state_row(group, _("Local certificate"), _("Available") if state.certificate_present and state.key_present else _("Missing"), state.certificate_present and state.key_present)
            self._add_state_row(group, _("Firmware enrollment"), _("Trusted by firmware") if state.enrolled else _("Not enrolled"), state.enrolled)
            module_summary = (
                _("All detected DKMS modules are trusted")
                if dkms.ready else
                _("Some DKMS modules need to be re-signed")
            )
            self._add_state_row(group, _("Third-party modules"), module_summary, dkms.ready)
        if state.enabled and not state.ready:
            button = Gtk.Button(label=_("Create or Enroll Certificate"), halign=Gtk.Align.END)
            button.add_css_class("suggested-action")
            button.connect("clicked", self._ask_mok_password)
            content.append(button)
        elif state.enabled:
            note = Adw.Banner(title=_("Secure Boot is ready for third-party drivers."))
            note.set_revealed(True); content.append(note)
            if not dkms.ready:
                repair = Gtk.Button(label=_("Repair Module Signatures"), halign=Gtk.Align.END)
                repair.add_css_class("suggested-action")
                repair.connect("clicked", lambda btn: self._run_action(btn, ["repair-dkms"]))
                content.append(repair)
        else:
            note = Adw.Banner(title=_("No certificate is required while Secure Boot is disabled."))
            note.set_revealed(True); content.append(note)
        return page

    def _add_state_row(self, group: Adw.PreferencesGroup, title: str, subtitle: str, good: bool | None) -> None:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if good is None:
            row.add_suffix(_status_icon("dialog-information-symbolic", "dim-label"))
        else:
            row.add_suffix(_status_icon("emblem-ok-symbolic" if good else "dialog-warning-symbolic", "success" if good else "warning"))
        group.add(row)

    def _ask_mok_password(self, button: Gtk.Button) -> None:
        dialog = Adw.MessageDialog(transient_for=self, heading=_("Secure Boot enrollment password"), body=_("Choose a temporary password. Enter it once in the blue MOKManager screen after reboot."))
        entry = Gtk.PasswordEntry(show_peek_icon=True)
        entry.set_placeholder_text(_("8–16 characters"))
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel")); dialog.add_response("continue", _("Continue"))
        dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("continue"); dialog.set_close_response("cancel")
        def response(_dialog: Adw.MessageDialog, name: str) -> None:
            if name == "continue":
                password = entry.get_text()
                if 8 <= len(password) <= 16:
                    self._run_action(button, ["enroll-mok"], password + "\n")
                else:
                    self._toast(_("The password must contain 8–16 characters."))
            dialog.close()
        dialog.connect("response", response); dialog.present()

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
