"""GTK4/libadwaita frontend for AnduinOS Control Panel."""

from __future__ import annotations

import gettext
from pathlib import Path
import subprocess
import sys
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from .model import (
    BOTTLES_APP_ID,
    DEJA_DUP_APP_ID,
    GRUB_DISPLAY_LARGE_TEXT,
    GRUB_DISPLAY_NATIVE,
    SNAPSHOT_PACKAGE,
    VOICE_TYPING_PACKAGE,
    WHY_AI_PACKAGE,
    WHY_PLACEHOLDER_PACKAGE,
    command_available,
    flatpak_installed,
    package_installed,
    read_grub_display_mode,
    read_grub_timeouts,
)
from .topics import ControlPanelTopic, get_topic


APP_ID = "com.anduinos.ControlPanel"
LOCALE_DIR = "/usr/share/locale"
FLATHUB_REMOTE = "flathub"
FLATHUB_REPOSITORY = "https://dl.flathub.org/repo/flathub.flatpakrepo"
BOOT_SETTINGS_HELPER = "/usr/libexec/anduinos-control-panel/boot-settings-helper"
gettext.bindtextdomain("anduinos-control-panel", LOCALE_DIR)
gettext.textdomain("anduinos-control-panel")
_ = gettext.gettext

ControlAction = tuple[str, str, Callable[[], None], tuple[str, ...]]


def _icon_path(name: str) -> Path:
    installed = Path("/usr/share/anduinos-control-panel/icons", name)
    if installed.is_file():
        return installed
    return Path(__file__).resolve().parents[2] / "resources" / "icons" / name


def _category_picture(name: str) -> Gtk.Picture:
    """Render SVGs with different intrinsic sizes into one 56 px boundary."""

    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
        str(_icon_path(name)), 56, 56, True
    )
    texture = Gdk.Texture.new_for_pixbuf(pixbuf)
    picture = Gtk.Picture.new_for_paintable(texture)
    picture.set_content_fit(Gtk.ContentFit.CONTAIN)
    picture.set_can_shrink(True)
    picture.set_size_request(56, 56)
    picture.set_halign(Gtk.Align.CENTER)
    picture.set_valign(Gtk.Align.CENTER)
    return picture


class ControlPanelWindow(Adw.ApplicationWindow):
    """Category-based system settings launcher."""

    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=_("Control Panel"))
        self.set_default_size(1166, 762)
        self.set_size_request(760, 560)
        self._category_children: list[Gtk.Widget] = []
        self._ai_window: Adw.Window | None = None
        self._flatseal_window: Adw.Window | None = None
        self._bottles_window: Adw.Window | None = None
        self._voice_install_window: Adw.Window | None = None
        self._boot_settings_window: Adw.Window | None = None

        self._install_css()

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        toolbar = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle.new(_("Control Panel"), _("AnduinOS")))
        toolbar.add_top_bar(header)

        self.search = Gtk.SearchEntry(
            placeholder_text=_("Search Control Panel"),
            tooltip_text=_("Search settings"),
        )
        self.search.set_size_request(260, -1)
        self.search.connect("search-changed", self._search_changed)
        header.pack_end(self.search)

        menu = Gio.Menu()
        menu.append(_("About Control Panel"), "app.about")
        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", tooltip_text=_("Main Menu")
        )
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroll.set_overlay_scrolling(False)
        toolbar.set_content(scroll)

        clamp = Adw.Clamp(maximum_size=800, tightening_threshold=720)
        scroll.set_child(clamp)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.set_margin_top(24)
        page.set_margin_bottom(28)
        page.set_margin_start(24)
        page.set_margin_end(24)
        clamp.set_child(page)

        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title = Gtk.Label(label=_("Adjust your computer's settings"), xalign=0)
        title.add_css_class("title-2")
        description = Gtk.Label(
            label=_("Choose a category below, or search for a setting."), xalign=0
        )
        description.add_css_class("dim-label")
        intro.append(title)
        intro.append(description)
        page.append(intro)

        self.grid = Gtk.Grid(
            column_homogeneous=True,
            column_spacing=42,
            row_spacing=18,
        )
        self.grid.set_valign(Gtk.Align.START)
        page.append(self.grid)

        self.empty = Adw.StatusPage(
            title=_("No settings found"),
            description=_("Try a different search."),
            icon_name="system-search-symbolic",
        )
        self.empty.set_visible(False)
        page.append(self.empty)

        self._rebuild_categories()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            .control-category {
                padding: 3px 2px;
            }
            .control-category-title {
                color: @success_color;
                font-size: 1.12em;
                font-weight: 700;
            }
            .control-action {
                min-height: 0;
                border: none;
                box-shadow: none;
                background: transparent;
                padding: 2px 0;
            }
            .control-action:hover {
                background: transparent;
            }
            .control-action-title {
                color: @accent_color;
                font-weight: 500;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _clear_grid(self) -> None:
        child = self.grid.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.grid.remove(child)
            child = next_child
        self._category_children.clear()

    def _rebuild_categories(self) -> None:
        self._clear_grid()
        why_installed = package_installed(WHY_AI_PACKAGE)
        bottles_installed = flatpak_installed(BOTTLES_APP_ID)
        flatseal_installed = package_installed("flatseal")
        deja_dup_installed = flatpak_installed(DEJA_DUP_APP_ID)
        seahorse_installed = command_available("seahorse")
        voice_typing_installed = package_installed(VOICE_TYPING_PACKAGE)

        def action(
            identifier: str, subtitle: str | None = None
        ) -> ControlAction:
            topic = get_topic(identifier)
            if topic is None:
                raise ValueError(f"Unknown Control Panel topic: {identifier}")
            return (
                topic.title,
                subtitle if subtitle is not None else topic.description,
                lambda topic_id=identifier: self._activate_topic(topic_id),
                topic.keywords,
            )

        system_actions = [
            action("system.settings"),
            action("system.startup-boot"),
            action("system.virtual-memory"),
        ]

        security_actions = [action("security.secure-boot")]
        if seahorse_installed:
            security_actions.append(action("security.passwords-keys"))

        network_actions = [
            action("network.firewall"),
            action(
                "network.advanced",
                (
                    _("Configure NetworkManager connection profiles")
                    if command_available("nm-connection-editor")
                    else _("Install Advanced Network Configuration")
                ),
            ),
        ]

        categories = [
            (
                _("System"),
                "preferences-system.svg",
                system_actions,
            ),
            (
                _("Security"),
                "com.anduinos.yubikeymanager.svg",
                security_actions,
            ),
            (
                _("Network and Internet"),
                "preferences-system-network-connection.svg",
                network_actions,
            ),
            (
                _("User Accounts"),
                "cs-user-accounts.svg",
                [
                    action("accounts.users"),
                    action("accounts.yubikey"),
                ],
            ),
            (
                _("Hardware and Drivers"),
                "com.anduinos.DriverCenter.svg",
                [
                    action("hardware.drivers"),
                    action("hardware.printers"),
                    action(
                        "hardware.scanners",
                        (
                            _("Scan documents and select a scanner")
                            if command_available("simple-scan")
                            else _("Install Document Scanner")
                        ),
                    ),
                ],
            ),
            (
                _("Appearance"),
                "anduinos-appearance.svg",
                [
                    action("appearance.anduinos"),
                    action("appearance.wallpaper"),
                ],
            ),
            (
                _("Programs"),
                "gnome-software.svg",
                [
                    action("programs.uninstall"),
                    action(
                        "programs.permissions",
                        (
                            _("Manage application permissions with Flatseal")
                            if flatseal_installed
                            else _("Install Flatseal to manage application permissions")
                        ),
                    ),
                ],
            ),
            (
                _("AI Stack"),
                "applications-science.svg",
                [
                    action(
                        "ai.on-device",
                        _("Installed") if why_installed else _("Not installed"),
                    ),
                    action(
                        "accessibility.voice-typing",
                        (
                            _("Configure microphone, language, shortcut, and training")
                            if voice_typing_installed
                            else _("Install private, offline speech-to-text")
                        ),
                    ),
                ],
            ),
            (
                _("Windows Compatibility"),
                "anduinos-exe-runner.svg",
                [
                    action(
                        "compatibility.windows",
                        (
                            _("Open your Windows application environments")
                            if bottles_installed
                            else _("Install Bottles to run Windows applications")
                        ),
                    ),
                ],
            ),
            (
                _("Backup and Recovery"),
                "preferences-system-backup.svg",
                self._backup_actions(deja_dup_installed),
            ),
        ]

        for title, icon, actions in categories:
            self._append_category(title, icon, actions)
        self._search_changed(self.search)

    def _backup_actions(
        self, deja_dup_installed: bool
    ) -> list[ControlAction]:
        actions: list[ControlAction] = []
        if package_installed(SNAPSHOT_PACKAGE):
            topic = get_topic("recovery.snapshots")
            if topic is not None:
                actions.append(
                    (
                        topic.title,
                        topic.description,
                        lambda: self._activate_topic("recovery.snapshots"),
                        topic.keywords,
                    )
                )
        backup = get_topic("recovery.backup")
        if backup is None:
            return actions
        actions.append(
            (
                backup.title,
                (
                    _("Open Deja Dup backups")
                    if deja_dup_installed
                    else _("Install Deja Dup from the app store")
                ),
                lambda: self._activate_topic("recovery.backup"),
                backup.keywords,
            )
        )
        return actions

    def _activate_topic(self, identifier: str) -> None:
        topic = get_topic(identifier)
        if topic is None:
            self._show_error(_("Setting not found"), identifier)
            return

        handlers: dict[str, Callable[[], None]] = {
            "boot-settings": self._show_boot_settings,
            "voice-typing": self._open_voice_typing,
            "flatseal": self._open_flatseal,
            "on-device-ai": self._show_ai_settings,
            "bottles": self._open_bottles,
            "backup": self._open_deja_dup,
        }
        if topic.handler:
            handler = handlers.get(topic.handler)
            if handler is None:
                self._show_error(_("Setting not found"), topic.title)
                return
            handler()
            return

        if topic.install_package and topic.command:
            if not command_available(topic.command[0]):
                self._offer_recommended_install(topic)
                return

        if topic.command:
            self._launch(list(topic.command))
            return

        self._show_error(_("Setting not found"), topic.title)

    def _append_category(
        self,
        title: str,
        icon_name: str,
        actions: list[ControlAction],
    ) -> None:
        root = Gtk.Grid(column_spacing=14)
        root.add_css_class("control-category")
        root.set_hexpand(True)
        root.set_halign(Gtk.Align.FILL)
        root.set_valign(Gtk.Align.START)

        icon_frame = Gtk.Box()
        icon_frame.set_size_request(60, 60)
        icon_frame.set_halign(Gtk.Align.START)
        icon_frame.set_valign(Gtk.Align.START)
        icon_frame.append(_category_picture(icon_name))
        root.attach(icon_frame, 0, 0, 1, 1)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        body.set_hexpand(True)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("control-category-title")
        heading.set_margin_bottom(2)
        body.append(heading)

        search_parts = [title]
        for action_title, subtitle, callback, keywords in actions:
            body.append(self._action_button(action_title, subtitle, callback))
            search_parts.extend((action_title, subtitle, *keywords))
        root.attach(body, 1, 0, 1, 1)

        root.search_text = " ".join(search_parts).casefold()
        self._category_children.append(root)

    def _action_button(
        self, title: str, subtitle: str, callback: Callable[[], None]
    ) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("control-action")
        button.set_halign(Gtk.Align.START)
        button.set_cursor_from_name("pointer")
        button.set_tooltip_text(subtitle)
        button.connect("clicked", lambda _button: callback())
        name = Gtk.Label(label=title, xalign=0)
        name.add_css_class("control-action-title")
        button.set_child(name)

        plain_attributes = Pango.AttrList()
        underlined_attributes = Pango.AttrList()
        underlined_attributes.insert(
            Pango.attr_underline_new(Pango.Underline.SINGLE)
        )
        interaction = {"hovered": False}

        def update_underline() -> None:
            attributes = (
                underlined_attributes
                if interaction["hovered"] or button.has_focus()
                else plain_attributes
            )
            name.set_attributes(attributes)

        def pointer_entered(
            _controller: Gtk.EventControllerMotion, _x: float, _y: float
        ) -> None:
            interaction["hovered"] = True
            update_underline()

        def pointer_left(_controller: Gtk.EventControllerMotion) -> None:
            interaction["hovered"] = False
            update_underline()

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", pointer_entered)
        motion.connect("leave", pointer_left)
        button.add_controller(motion)
        button.connect("notify::has-focus", lambda *_args: update_underline())
        return button

    def _search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip().casefold()
        child = self.grid.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.grid.remove(child)
            child = next_child

        visible_children = []
        for child in self._category_children:
            if not query or query in child.search_text:
                visible_children.append(child)
        for index, child in enumerate(visible_children):
            self.grid.attach(child, index % 2, index // 2, 1, 1)
        self.grid.set_visible(bool(visible_children))
        self.empty.set_visible(not visible_children)

    def _launch(self, arguments: list[str]) -> None:
        try:
            process = Gio.Subprocess.new(
                arguments,
                Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_PIPE,
            )
        except GLib.Error as error:
            self._show_error(_("Could not open this setting"), str(error))
            return

        def launch_completed(
            launched_process: Gio.Subprocess, result: Gio.AsyncResult
        ) -> None:
            try:
                _communicated, stdout, stderr = launched_process.communicate_utf8_finish(
                    result
                )
            except GLib.Error as error:
                self._show_error(_("Could not open this setting"), str(error))
                return

            if launched_process.get_successful():
                return

            details = (stderr or stdout or "").strip()
            if not details:
                details = _("The application exited before it could be opened.")
            self._show_error(_("Could not open this setting"), details)

        process.communicate_utf8_async(None, None, launch_completed)

    def _show_error(self, heading: str, body: str = "") -> None:
        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("ok", _("OK"))
        dialog.present()

    def _offer_recommended_install(self, topic: ControlPanelTopic) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Install %s?") % topic.title,
            body=_(
                "%s is a recommended component, but it is not installed. "
                "You can reinstall it now."
            )
            % topic.title,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Install"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)

        def response_received(_dialog: Adw.MessageDialog, response: str) -> None:
            if response != "install":
                return
            self.toast_overlay.add_toast(
                Adw.Toast.new(_("Installing %s…") % topic.title)
            )

            def completed(return_code: int, output: str) -> bool:
                if return_code == 0 and topic.command and command_available(
                    topic.command[0]
                ):
                    self._rebuild_categories()
                    self.toast_overlay.add_toast(
                        Adw.Toast.new(_("%s installed") % topic.title)
                    )
                    self._launch(list(topic.command))
                    return GLib.SOURCE_REMOVE

                message = output.strip()
                if return_code == 126:
                    message = _("Authentication was cancelled.")
                elif not message:
                    message = _("The recommended component could not be installed.")
                self._show_error(_("Installation failed"), message)
                return GLib.SOURCE_REMOVE

            def worker() -> None:
                try:
                    result = subprocess.run(
                        [
                            "/usr/bin/pkexec",
                            "/usr/bin/apt-get",
                            "install",
                            "--yes",
                            topic.install_package,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=1800,
                        check=False,
                    )
                    GLib.idle_add(
                        completed,
                        result.returncode,
                        result.stderr or result.stdout,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    GLib.idle_add(completed, 1, str(error))

            threading.Thread(target=worker, daemon=True).start()

        dialog.connect("response", response_received)
        dialog.present()

    def _show_boot_settings(self) -> None:
        if self._boot_settings_window is not None:
            self._boot_settings_window.present()
            return

        current = read_grub_timeouts()
        current_display_mode = read_grub_display_mode()
        state = {
            "normal": current.normal,
            "after_interrupted_boot": current.after_interrupted_boot,
            "display_mode": current_display_mode,
        }
        choices = sorted({0, 3, 5, 10, 30, current.normal})
        window = Adw.Window(
            transient_for=self,
            modal=True,
            title=_("Startup and Boot"),
            default_width=560,
            default_height=460,
        )
        self._boot_settings_window = window
        window.connect("close-request", self._boot_settings_window_closed)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        window.set_content(toolbar)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(24)
        page.set_margin_end(24)
        toolbar.set_content(page)

        group = Adw.PreferencesGroup(
            title=_("Boot menu"),
            description=_(
                "Choose how long the boot menu waits before starting the default system."
            ),
        )
        timeout_row = Adw.ComboRow(
            title=_("Boot menu wait time"),
            subtitle=_(
                "The same delay is used after an interrupted or unsuccessful startup."
            ),
        )
        timeout_row.add_prefix(
            Gtk.Image.new_from_icon_name("system-reboot-symbolic")
        )
        timeout_row.set_model(
            Gtk.StringList.new([_("%d seconds") % value for value in choices])
        )
        timeout_row.set_selected(choices.index(current.normal))
        group.add(timeout_row)
        page.append(group)

        display_modes = [GRUB_DISPLAY_NATIVE, GRUB_DISPLAY_LARGE_TEXT]
        display_group = Adw.PreferencesGroup(
            title=_("Boot display"),
            description=_(
                "Choose the size of the GRUB menu and startup logo."
            ),
        )
        display_row = Adw.ComboRow(
            title=_("Display mode"),
            subtitle=_(
                "Native resolution shows more detail; large text is easier to read."
            ),
        )
        display_row.add_prefix(
            Gtk.Image.new_from_icon_name("video-display-symbolic")
        )
        display_row.set_model(
            Gtk.StringList.new(
                [_("Native resolution"), _("Large text mode")]
            )
        )
        display_row.set_selected(display_modes.index(current_display_mode))
        display_group.add(display_row)
        page.append(display_group)

        if current.normal == current.after_interrupted_boot:
            current_text = _("Current setting: %d seconds") % current.normal
        else:
            current_text = _(
                "Current setting: %d seconds; after an interrupted startup: %d seconds"
            ) % (current.normal, current.after_interrupted_boot)
        status = Gtk.Label(label=current_text, xalign=0, wrap=True)
        status.add_css_class("dim-label")
        page.append(status)

        spinner = Gtk.Spinner()
        spinner.set_halign(Gtk.Align.START)
        spinner.set_visible(False)
        page.append(spinner)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.END)
        close = Gtk.Button(label=_("Close"))
        close.connect("clicked", lambda _button: window.close())
        apply = Gtk.Button(label=_("Apply"))
        apply.add_css_class("suggested-action")

        def selection_changed(_row: Adw.ComboRow, _parameter) -> None:
            selected = choices[timeout_row.get_selected()]
            display_mode = display_modes[display_row.get_selected()]
            apply.set_sensitive(
                selected != state["normal"]
                or selected != state["after_interrupted_boot"]
                or display_mode != state["display_mode"]
            )

        timeout_row.connect("notify::selected", selection_changed)
        display_row.connect("notify::selected", selection_changed)
        selection_changed(timeout_row, None)
        apply.connect(
            "clicked",
            lambda _button: self._apply_boot_settings(
                choices[timeout_row.get_selected()],
                display_modes[display_row.get_selected()],
                window,
                timeout_row,
                display_row,
                apply,
                close,
                status,
                spinner,
                state,
            ),
        )
        buttons.append(close)
        buttons.append(apply)
        page.append(buttons)
        window.present()

    def _boot_settings_window_closed(self, _window: Adw.Window) -> bool:
        self._boot_settings_window = None
        return False

    def _apply_boot_settings(
        self,
        timeout: int,
        display_mode: str,
        window: Adw.Window,
        timeout_row: Adw.ComboRow,
        display_row: Adw.ComboRow,
        apply: Gtk.Button,
        close: Gtk.Button,
        status: Gtk.Label,
        spinner: Gtk.Spinner,
        state: dict[str, int | str],
    ) -> None:
        timeout_row.set_sensitive(False)
        display_row.set_sensitive(False)
        apply.set_sensitive(False)
        close.set_sensitive(False)
        window.set_deletable(False)
        spinner.set_visible(True)
        spinner.start()
        status.set_label(_("Updating the boot menu…"))

        def completed(return_code: int, output: str) -> bool:
            spinner.stop()
            spinner.set_visible(False)
            timeout_row.set_sensitive(True)
            display_row.set_sensitive(True)
            close.set_sensitive(True)
            window.set_deletable(True)
            if return_code == 0:
                state["normal"] = timeout
                state["after_interrupted_boot"] = timeout
                state["display_mode"] = display_mode
                status.set_label(
                    _("Boot settings updated. The change applies on the next startup.")
                )
                self.toast_overlay.add_toast(
                    Adw.Toast.new(_("Boot settings updated"))
                )
            else:
                message = output.strip()
                if return_code == 126:
                    message = _("Authentication was cancelled.")
                elif not message:
                    message = _("The boot setting could not be changed.")
                status.set_label(message)
                apply.set_sensitive(True)
            return GLib.SOURCE_REMOVE

        def worker() -> None:
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/pkexec",
                        BOOT_SETTINGS_HELPER,
                        "set-settings",
                        str(timeout),
                        display_mode,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                output = result.stderr or result.stdout
                GLib.idle_add(completed, result.returncode, output)
            except (OSError, subprocess.TimeoutExpired) as error:
                GLib.idle_add(completed, 1, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _show_store_prompt(self, application_name: str, software_id: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Install %s?") % application_name,
            body=_("This application is available from the app store."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("store", _("Open App Store"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("store")
        dialog.set_response_appearance("store", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _dialog, response: self._launch(
                ["gnome-software", f"--details={software_id}"]
            )
            if response == "store"
            else None,
        )
        dialog.present()

    def _open_bottles(self) -> None:
        if flatpak_installed(BOTTLES_APP_ID):
            self._launch(["flatpak", "run", BOTTLES_APP_ID])
            return

        if self._bottles_window is not None:
            self._bottles_window.present()
            return

        window = Adw.Window(
            transient_for=self,
            modal=True,
            title=_("Windows Compatibility"),
            default_width=560,
            default_height=360,
        )
        self._bottles_window = window
        window.connect("close-request", self._bottles_window_closed)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        window.set_content(toolbar)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(24)
        page.set_margin_end(24)
        toolbar.set_content(page)

        group = Adw.PreferencesGroup(
            title=_("Bottles"),
            description=_(
                "Run Windows applications in isolated compatibility environments "
                "powered by Wine and Bottles."
            ),
        )
        install_row = Adw.ActionRow(
            title=_("Install Bottles"),
            subtitle=_("Downloads Bottles and its runtime from Flathub"),
        )
        install_row.add_prefix(
            Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        )
        group.add(install_row)
        page.append(group)

        status_label = Gtk.Label(xalign=0, wrap=True)
        status_label.add_css_class("dim-label")
        page.append(status_label)

        progress = Gtk.ProgressBar()
        progress.set_visible(False)
        page.append(progress)

        expander = Gtk.Expander(label=_("Advanced Output"))
        expander.set_hexpand(True)
        output_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=180,
        )
        output_scroll.set_overlay_scrolling(False)
        output = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        output.add_css_class("card")
        output_scroll.set_child(output)
        expander.set_child(output_scroll)

        def advanced_output_toggled(row: Gtk.Expander, _parameter) -> None:
            window.set_default_size(
                680 if row.get_expanded() else 560,
                560 if row.get_expanded() else 360,
            )

        expander.connect("notify::expanded", advanced_output_toggled)
        page.append(expander)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: window.close())
        start = Gtk.Button(label=_("Start"))
        start.add_css_class("suggested-action")
        state = {"installed": False}

        def start_clicked(_button: Gtk.Button) -> None:
            if state["installed"]:
                window.close()
                self._launch(["flatpak", "run", BOTTLES_APP_ID])
                return
            self._install_bottles(
                window,
                start,
                cancel,
                status_label,
                progress,
                output,
                state,
            )

        start.connect("clicked", start_clicked)
        buttons.append(cancel)
        buttons.append(start)
        page.append(buttons)
        window.present()

    def _bottles_window_closed(self, _window: Adw.Window) -> bool:
        self._bottles_window = None
        return False

    def _install_bottles(
        self,
        window: Adw.Window,
        start: Gtk.Button,
        cancel: Gtk.Button,
        status_label: Gtk.Label,
        progress: Gtk.ProgressBar,
        output: Gtk.TextView,
        state: dict[str, bool],
    ) -> None:
        start.set_sensitive(False)
        cancel.set_sensitive(False)
        window.set_deletable(False)
        start.set_label(_("Installing…"))
        status_label.set_label(
            _("Downloading and installing Bottles… This may take a few minutes.")
        )
        progress.set_visible(True)
        progress.pulse()
        buffer = output.get_buffer()
        buffer.set_text(_("Preparing Flathub and Bottles installation…") + "\n\n")

        def pulse() -> bool:
            if progress.get_visible():
                progress.pulse()
                return GLib.SOURCE_CONTINUE
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, pulse)

        def completed() -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            state["installed"] = True
            status_label.set_label(_("✓ Bottles is ready."))
            self._append_package_output(
                buffer,
                output,
                "\n" + _("✓ Installation completed successfully.") + "\n",
            )
            start.set_label(_("Open Bottles"))
            start.set_sensitive(True)
            cancel.set_label(_("Close"))
            cancel.set_sensitive(True)
            self._rebuild_categories()
            self.toast_overlay.add_toast(Adw.Toast.new(_("Bottles installed")))

        def failed(message: str) -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            status_label.set_label(
                _("✗ Installation failed. Review Advanced Output.")
            )
            self._append_package_output(
                buffer,
                output,
                "\n" + _("✗ Operation failed: ") + message + "\n",
            )
            start.set_label(_("Retry"))
            start.set_sensitive(True)
            cancel.set_sensitive(True)

        commands = [
            [
                "/usr/bin/flatpak",
                "remote-add",
                "--if-not-exists",
                "--system",
                FLATHUB_REMOTE,
                FLATHUB_REPOSITORY,
            ],
            [
                "/usr/bin/flatpak",
                "install",
                "--system",
                "--assumeyes",
                FLATHUB_REMOTE,
                BOTTLES_APP_ID,
            ],
        ]
        self._run_streaming_commands(
            commands, buffer, output, completed, failed
        )

    def _open_deja_dup(self) -> None:
        if flatpak_installed(DEJA_DUP_APP_ID):
            self._launch(["flatpak", "run", DEJA_DUP_APP_ID])
            return
        self._show_store_prompt(_("Deja Dup Backups"), f"{DEJA_DUP_APP_ID}.desktop")

    def _open_flatseal(self) -> None:
        if package_installed("flatseal"):
            self._launch(["com.github.tchx84.Flatseal"])
            return

        if self._flatseal_window is not None:
            self._flatseal_window.present()
            return

        window = Adw.Window(
            transient_for=self,
            modal=True,
            title=_("Permission Settings"),
            default_width=560,
            default_height=360,
        )
        self._flatseal_window = window
        window.connect("close-request", self._flatseal_window_closed)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        window.set_content(toolbar)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(24)
        page.set_margin_end(24)
        toolbar.set_content(page)

        group = Adw.PreferencesGroup(
            title=_("Flatseal"),
            description=_(
                "Review and control what files, devices, network connections, "
                "and services Flatpak applications can access."
            ),
        )
        install_row = Adw.ActionRow(
            title=_("Install Flatseal"),
            subtitle=_(
                "Administrator authentication is required when installation starts"
            ),
        )
        install_row.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        group.add(install_row)
        page.append(group)

        status_label = Gtk.Label(xalign=0, wrap=True)
        status_label.add_css_class("dim-label")
        page.append(status_label)

        progress = Gtk.ProgressBar()
        progress.set_visible(False)
        page.append(progress)

        expander = Gtk.Expander(label=_("Advanced Output"))
        expander.set_hexpand(True)
        output_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=180,
        )
        output_scroll.set_overlay_scrolling(False)
        output = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        output.add_css_class("card")
        output_scroll.set_child(output)
        expander.set_child(output_scroll)

        def advanced_output_toggled(row: Gtk.Expander, _parameter) -> None:
            window.set_default_size(
                680 if row.get_expanded() else 560,
                560 if row.get_expanded() else 360,
            )

        expander.connect("notify::expanded", advanced_output_toggled)
        page.append(expander)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: window.close())
        start = Gtk.Button(label=_("Start"))
        start.add_css_class("suggested-action")
        state = {"installed": False}

        def start_clicked(_button: Gtk.Button) -> None:
            if state["installed"]:
                window.close()
                self._launch(["com.github.tchx84.Flatseal"])
                return
            self._install_flatseal(
                window,
                start,
                cancel,
                status_label,
                progress,
                output,
                state,
            )

        start.connect("clicked", start_clicked)
        buttons.append(cancel)
        buttons.append(start)
        page.append(buttons)
        window.present()

    def _flatseal_window_closed(self, _window: Adw.Window) -> bool:
        self._flatseal_window = None
        return False

    def _install_flatseal(
        self,
        window: Adw.Window,
        start: Gtk.Button,
        cancel: Gtk.Button,
        status_label: Gtk.Label,
        progress: Gtk.ProgressBar,
        output: Gtk.TextView,
        state: dict[str, bool],
    ) -> None:
        start.set_sensitive(False)
        cancel.set_sensitive(False)
        window.set_deletable(False)
        start.set_label(_("Installing…"))
        status_label.set_label(_("Downloading and installing Flatseal…"))
        progress.set_visible(True)
        progress.pulse()
        buffer = output.get_buffer()
        buffer.set_text(
            _("Preparing Flatseal installation…") + "\n\n"
        )

        def pulse() -> bool:
            if progress.get_visible():
                progress.pulse()
                return GLib.SOURCE_CONTINUE
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, pulse)

        def completed() -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            state["installed"] = True
            status_label.set_label(_("✓ Flatseal is ready."))
            self._append_package_output(
                buffer,
                output,
                "\n" + _("✓ Installation completed successfully.") + "\n",
            )
            start.set_label(_("Open Flatseal"))
            start.set_sensitive(True)
            cancel.set_label(_("Close"))
            cancel.set_sensitive(True)
            self._rebuild_categories()
            self.toast_overlay.add_toast(Adw.Toast.new(_("Flatseal installed")))

        def failed(message: str) -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            status_label.set_label(
                _("✗ Installation failed. Review Advanced Output.")
            )
            self._append_package_output(
                buffer,
                output,
                "\n" + _("✗ Operation failed: ") + message + "\n",
            )
            start.set_label(_("Retry"))
            start.set_sensitive(True)
            cancel.set_sensitive(True)

        self._run_streaming_package_change(
            "flatseal", buffer, output, completed, failed
        )

    def _open_voice_typing(self) -> None:
        if package_installed(VOICE_TYPING_PACKAGE):
            self._launch(["anduinos-whisper-gtk"])
            return
        if self._voice_install_window is not None:
            self._voice_install_window.present()
            return

        window = Adw.Window(
            transient_for=self,
            modal=True,
            title=_("Install Voice Typing"),
            default_width=580,
            default_height=430,
        )
        self._voice_install_window = window
        window.connect("close-request", self._voice_install_window_closed)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        window.set_content(toolbar)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(24)
        page.set_margin_end(24)
        toolbar.set_content(page)

        group = Adw.PreferencesGroup(
            title=_("Offline Voice Typing"),
            description=_(
                "Press Super + H to dictate into browsers, editors, messages, "
                "terminals, and other applications. Audio stays on this computer."
            ),
        )
        privacy = Adw.ActionRow(
            title=_("Private by default"),
            subtitle=_("Speech is recognized locally with whisper.cpp"),
        )
        privacy.add_prefix(Gtk.Image.new_from_icon_name("security-high-symbolic"))
        group.add(privacy)
        download = Adw.ActionRow(
            title=_("About 140 MB to download"),
            subtitle=_(
                "Includes the multilingual Base model; optional models are available later"
            ),
        )
        download.add_prefix(Gtk.Image.new_from_icon_name("folder-download-symbolic"))
        group.add(download)
        page.append(group)

        status_label = Gtk.Label(
            label=_(
                "Administrator authentication is required when installation starts."
            ),
            xalign=0,
            wrap=True,
        )
        status_label.add_css_class("dim-label")
        page.append(status_label)
        progress = Gtk.ProgressBar()
        progress.set_visible(False)
        page.append(progress)

        expander = Gtk.Expander(label=_("Advanced Output"))
        output_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=170,
        )
        output = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        output.add_css_class("card")
        output_scroll.set_child(output)
        expander.set_child(output_scroll)

        def advanced_output_toggled(row: Gtk.Expander, _parameter) -> None:
            window.set_default_size(
                700 if row.get_expanded() else 580,
                620 if row.get_expanded() else 430,
            )

        expander.connect("notify::expanded", advanced_output_toggled)
        page.append(expander)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: window.close())
        install = Gtk.Button(label=_("Install"))
        install.add_css_class("suggested-action")
        install.connect(
            "clicked",
            lambda button: (
                self._launch(["anduinos-whisper-gtk"])
                if package_installed(VOICE_TYPING_PACKAGE)
                else self._install_voice_typing(
                    window,
                    button,
                    cancel,
                    status_label,
                    progress,
                    expander,
                    output,
                )
            ),
        )
        buttons.append(cancel)
        buttons.append(install)
        page.append(buttons)
        window.present()

    def _voice_install_window_closed(self, _window: Adw.Window) -> bool:
        self._voice_install_window = None
        return False

    def _install_voice_typing(
        self,
        window: Adw.Window,
        install: Gtk.Button,
        cancel: Gtk.Button,
        status_label: Gtk.Label,
        progress: Gtk.ProgressBar,
        expander: Gtk.Expander,
        output: Gtk.TextView,
    ) -> None:
        install.set_label(_("Installing…"))
        install.set_sensitive(False)
        cancel.set_sensitive(False)
        window.set_deletable(False)
        status_label.set_label(
            _("Downloading the local engine and multilingual speech model…")
        )
        progress.set_visible(True)
        progress.pulse()
        expander.set_expanded(True)
        buffer = output.get_buffer()
        buffer.set_text(_("Preparing Voice Typing installation…") + "\n\n")

        def pulse() -> bool:
            if progress.get_visible():
                progress.pulse()
                return GLib.SOURCE_CONTINUE
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, pulse)

        def completed() -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            self._append_package_output(
                buffer,
                output,
                "\n" + _("✓ Installation completed successfully.") + "\n",
            )
            shell_settings = Gio.Settings.new("org.gnome.shell")
            extension_uuid = "voice-typing@anduinos.com"
            enabled_extensions = shell_settings.get_strv("enabled-extensions")
            if extension_uuid not in enabled_extensions:
                shell_settings.set_strv(
                    "enabled-extensions", [*enabled_extensions, extension_uuid]
                )
            disabled_extensions = shell_settings.get_strv("disabled-extensions")
            if extension_uuid in disabled_extensions:
                shell_settings.set_strv(
                    "disabled-extensions",
                    [item for item in disabled_extensions if item != extension_uuid],
                )
            try:
                subprocess.run(
                    ["gnome-extensions", "enable", "--quiet", extension_uuid],
                    check=False,
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            status_label.set_label(
                _(
                    "✓ Installed. Sign out and back in once, then press "
                    "Super + H to start Voice Typing."
                )
            )
            install.set_label(_("Open Settings"))
            install.set_sensitive(True)
            cancel.set_label(_("Close"))
            cancel.set_sensitive(True)
            self._rebuild_categories()
            self.toast_overlay.add_toast(
                Adw.Toast.new(_("Voice Typing installed — sign out to finish"))
            )

        def failed(message: str) -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            status_label.set_label(
                _("✗ Installation failed. Review Advanced Output.")
            )
            self._append_package_output(
                buffer,
                output,
                "\n" + _("✗ Operation failed: ") + message + "\n",
            )
            install.set_label(_("Retry"))
            install.set_sensitive(True)
            cancel.set_sensitive(True)

        self._run_streaming_package_change(
            VOICE_TYPING_PACKAGE, buffer, output, completed, failed
        )

    def _show_ai_settings(self) -> None:
        if self._ai_window is not None:
            self._ai_window.present()
            return

        installed = package_installed(WHY_AI_PACKAGE)
        window = Adw.Window(
            transient_for=self,
            modal=True,
            title=_("On-device AI"),
            default_width=560,
            default_height=360,
        )
        self._ai_window = window
        window.connect("close-request", self._ai_window_closed)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        window.set_content(toolbar)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.set_margin_top(24)
        page.set_margin_bottom(24)
        page.set_margin_start(24)
        page.set_margin_end(24)
        toolbar.set_content(page)

        group = Adw.PreferencesGroup(
            title=_("Why AI"),
            description=_(
                "Run the Why AI assistant locally. The model and runtime are "
                "installed only when this option is enabled."
            ),
        )
        toggle = Adw.SwitchRow(
            title=_("Install on-device AI"),
            subtitle=_("Enable the local Why AI stack on this computer"),
        )
        toggle.set_active(installed)
        group.add(toggle)
        page.append(group)

        status_label = Gtk.Label(xalign=0, wrap=True)
        status_label.add_css_class("dim-label")
        page.append(status_label)

        progress = Gtk.ProgressBar()
        progress.set_visible(False)
        page.append(progress)

        expander = Gtk.Expander(label=_("Advanced Output"))
        expander.set_hexpand(True)
        output_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            min_content_height=180,
        )
        output_scroll.set_overlay_scrolling(False)
        output = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        output.add_css_class("card")
        output_scroll.set_child(output)
        expander.set_child(output_scroll)

        def advanced_output_toggled(row: Gtk.Expander, _parameter) -> None:
            window.set_default_size(
                680 if row.get_expanded() else 560,
                560 if row.get_expanded() else 360,
            )

        expander.connect("notify::expanded", advanced_output_toggled)
        page.append(expander)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(spacer)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda _button: window.close())
        apply = Gtk.Button(label=_("Apply"))
        apply.add_css_class("suggested-action")
        apply.set_sensitive(False)
        def toggle_changed(row: Adw.SwitchRow, _parameter) -> None:
            apply.set_label(_("Apply"))
            apply.set_sensitive(row.get_active() != installed)

        toggle.connect("notify::active", toggle_changed)
        apply.connect(
            "clicked",
            lambda button: self._apply_ai_setting(
                window,
                toggle,
                button,
                cancel,
                status_label,
                progress,
                expander,
                output,
            ),
        )
        buttons.append(cancel)
        buttons.append(apply)
        page.append(buttons)
        window.present()

    def _ai_window_closed(self, _window: Adw.Window) -> bool:
        self._ai_window = None
        return False

    def _apply_ai_setting(
        self,
        window: Adw.Window,
        toggle: Adw.SwitchRow,
        apply: Gtk.Button,
        cancel: Gtk.Button,
        status_label: Gtk.Label,
        progress: Gtk.ProgressBar,
        expander: Gtk.Expander,
        output: Gtk.TextView,
    ) -> None:
        enabled = toggle.get_active()
        apply.set_sensitive(False)
        cancel.set_sensitive(False)
        toggle.set_sensitive(False)
        window.set_deletable(False)
        apply.set_label(_("Applying…"))
        status_label.set_label(
            _("Downloading and installing Why AI… This may take about 10 minutes.")
            if enabled
            else _("Disabling the local Why AI stack…")
        )
        progress.set_visible(True)
        progress.pulse()
        expander.set_expanded(True)
        package = WHY_AI_PACKAGE if enabled else WHY_PLACEHOLDER_PACKAGE
        buffer = output.get_buffer()
        buffer.set_text(_("Preparing package operation…") + "\n\n")

        def pulse() -> bool:
            if progress.get_visible():
                progress.pulse()
                return GLib.SOURCE_CONTINUE
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, pulse)

        def completed() -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            status_label.set_label(
                _("✓ On-device AI is ready.")
                if enabled
                else _("✓ On-device AI is disabled.")
            )
            self._append_package_output(
                buffer,
                output,
                "\n"
                + (_("✓ Installation completed successfully.") if enabled else _("✓ Change completed successfully."))
                + "\n",
            )
            apply.set_visible(False)
            cancel.set_label(_("Close"))
            cancel.set_sensitive(True)
            self._rebuild_categories()
            self.toast_overlay.add_toast(
                Adw.Toast.new(
                    _("On-device AI enabled")
                    if enabled
                    else _("On-device AI disabled")
                )
            )

        def failed(message: str) -> None:
            progress.set_visible(False)
            window.set_deletable(True)
            toggle.set_sensitive(True)
            status_label.set_label(_("✗ Package operation failed. Review Advanced Output."))
            self._append_package_output(
                buffer,
                output,
                "\n" + _("✗ Operation failed: ") + message + "\n",
            )
            apply.set_label(_("Retry"))
            apply.set_sensitive(True)
            cancel.set_sensitive(True)

        self._run_streaming_package_change(
            package, buffer, output, completed, failed
        )

    @staticmethod
    def _append_package_output(
        buffer: Gtk.TextBuffer, output: Gtk.TextView, text: str
    ) -> bool:
        buffer.insert(buffer.get_end_iter(), text)
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        output.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        return GLib.SOURCE_REMOVE

    def _run_streaming_package_change(
        self,
        package: str,
        buffer: Gtk.TextBuffer,
        output: Gtk.TextView,
        success: Callable[[], None],
        failure: Callable[[str], None],
    ) -> None:
        arguments = [
            "/usr/bin/pkexec",
            "/usr/bin/apt-get",
            "install",
            "--yes",
            package,
        ]
        self._run_streaming_commands(
            [arguments], buffer, output, success, failure
        )

    def _run_streaming_commands(
        self,
        commands: list[list[str]],
        buffer: Gtk.TextBuffer,
        output: Gtk.TextView,
        success: Callable[[], None],
        failure: Callable[[str], None],
    ) -> None:

        def worker() -> None:
            try:
                for arguments in commands:
                    GLib.idle_add(
                        self._append_package_output,
                        buffer,
                        output,
                        "$ " + " ".join(arguments) + "\n",
                    )
                    process = subprocess.Popen(
                        arguments,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    if process.stdout is not None:
                        for line in iter(process.stdout.readline, ""):
                            GLib.idle_add(
                                self._append_package_output, buffer, output, line
                            )
                        process.stdout.close()
                    return_code = process.wait()
                    if return_code != 0:
                        GLib.idle_add(
                            failure,
                            _("%s exited with status %d")
                            % (Path(arguments[0]).name, return_code),
                        )
                        return
                    GLib.idle_add(
                        self._append_package_output, buffer, output, "\n"
                    )
                GLib.idle_add(success)
            except Exception as error:
                GLib.idle_add(failure, str(error))

        threading.Thread(target=worker, daemon=True).start()


class ControlPanelApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.add_main_option(
            "topic",
            ord("t"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            _("Open a Control Panel topic"),
            _("TOPIC"),
        )
        self.add_main_option(
            "search",
            ord("s"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            _("Search Control Panel"),
            _("TERMS"),
        )

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._show_about)
        self.add_action(about)

    def do_activate(self) -> None:
        window = self.get_active_window() or ControlPanelWindow(self)
        window.present()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        topic_value = options.lookup_value("topic", GLib.VariantType.new("s"))
        search_value = options.lookup_value("search", GLib.VariantType.new("s"))

        self.activate()
        window = self.get_active_window()
        if not isinstance(window, ControlPanelWindow):
            return 1

        if search_value is not None:
            window.search.set_text(search_value.unpack())
            window.search.grab_focus()

        if topic_value is not None:
            identifier = topic_value.unpack()
            if get_topic(identifier) is None:
                window._show_error(_("Setting not found"), identifier)
                return 2
            window._activate_topic(identifier)
        return 0

    def _show_about(self, _action: Gio.SimpleAction, _parameter) -> None:
        dialog = Adw.AboutDialog()
        dialog.set_application_name(_("AnduinOS Control Panel"))
        dialog.set_application_icon(APP_ID)
        dialog.set_developer_name(_("AnduinOS Team"))
        dialog.set_version("2.0.2")
        dialog.set_comments(_("Find and manage AnduinOS system settings."))
        dialog.set_website("https://www.anduinos.com")
        dialog.set_issue_url(
            "https://github.com/AiursoftWeb/AnduinOS-Packages/issues"
        )
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_copyright("© 2026 AnduinOS Team")
        dialog.present(self.get_active_window())


def main() -> int:
    Adw.init()
    return ControlPanelApplication().run(sys.argv)
