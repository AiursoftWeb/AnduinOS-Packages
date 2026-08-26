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
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .model import (
    BOTTLES_APP_ID,
    DEJA_DUP_APP_ID,
    SNAPSHOT_PACKAGE,
    WHY_AI_PACKAGE,
    WHY_PLACEHOLDER_PACKAGE,
    command_available,
    flatpak_installed,
    package_installed,
)


APP_ID = "com.anduinos.ControlPanel"
LOCALE_DIR = "/usr/share/locale"
gettext.bindtextdomain("anduinos-control-panel", LOCALE_DIR)
gettext.textdomain("anduinos-control-panel")
_ = gettext.gettext


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

        refresh = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Refresh availability")
        )
        refresh.connect("clicked", lambda _button: self._rebuild_categories())
        header.pack_end(refresh)

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
                outline: none;
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
        network_editor_installed = command_available("nm-connection-editor")
        seahorse_installed = command_available("seahorse")

        system_actions = [
            (
                _("System Settings"),
                _("Display, sound, power, privacy, and more"),
                lambda: self._launch(["gnome-control-center"]),
            ),
            (
                _("Virtual Memory Settings"),
                _("Configure Zram, Zswap, swap, and memory pressure"),
                lambda: self._launch(["swapcontrol-gtk"]),
            ),
        ]

        security_actions = [
            (
                _("Secure Boot Status"),
                _("Inspect firmware trust and signed drivers"),
                lambda: self._launch(
                    ["anduinos-driver-center", "--page", "secure-boot"]
                ),
            )
        ]
        if seahorse_installed:
            security_actions.append(
                (
                    _("Passwords and Keys"),
                    _("Manage passwords, encryption keys, and certificates"),
                    lambda: self._launch(["seahorse"]),
                )
            )

        network_actions = [
            (
                _("Firewall"),
                _("Review connections, rules, and network protection"),
                lambda: self._launch(["ufwall-gtk"]),
            )
        ]
        if network_editor_installed:
            network_actions.append(
                (
                    _("Advanced Network Configuration"),
                    _("Configure NetworkManager connection profiles"),
                    lambda: self._launch(["nm-connection-editor"]),
                )
            )

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
                    (
                        _("User Account Settings"),
                        _("Manage users, passwords, and account details"),
                        lambda: self._launch(
                            ["gnome-control-center", "system", "users"]
                        ),
                    ),
                    (
                        _("YubiKey Settings"),
                        _("Configure sign-in, sudo, SSH keys, and Git signing"),
                        lambda: self._launch(["anduinos-yubikey-manager"]),
                    ),
                ],
            ),
            (
                _("Hardware and Drivers"),
                "com.anduinos.DriverCenter.svg",
                [
                    (
                        _("Driver Center"),
                        _("Graphics, audio, printers, controllers, and firmware"),
                        lambda: self._launch(["anduinos-driver-center"]),
                    ),
                    (
                        _("Printers"),
                        _("Add, remove, and configure printers"),
                        lambda: self._launch(["gnome-control-center", "printers"]),
                    ),
                ],
            ),
            (
                _("Appearance"),
                "anduinos-appearance.svg",
                [
                    (
                        _("AnduinOS Appearance Settings"),
                        _("Configure the taskbar, panel widgets, and desktop"),
                        lambda: self._launch(["anduinos-appearance"]),
                    )
                ],
            ),
            (
                _("Programs"),
                "gnome-software.svg",
                [
                    (
                        _("Uninstall Applications"),
                        _("Review and remove installed applications"),
                        lambda: self._launch(["gnome-software", "--mode=installed"]),
                    ),
                    (
                        _("Permission Settings"),
                        (
                            _("Manage application permissions with Flatseal")
                            if flatseal_installed
                            else _("Install Flatseal to manage application permissions")
                        ),
                        self._open_flatseal,
                    ),
                ],
            ),
            (
                _("AI Stack"),
                "applications-science.svg",
                [
                    (
                        _("On-device AI"),
                        _("Installed") if why_installed else _("Not installed"),
                        self._show_ai_settings,
                    ),
                ],
            ),
            (
                _("Windows Compatibility"),
                "anduinos-exe-runner.svg",
                [
                    (
                        _("Configure Bottles"),
                        (
                            _("Open your Windows application environments")
                            if bottles_installed
                            else _("Install Bottles from the app store")
                        ),
                        self._open_bottles,
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
    ) -> list[tuple[str, str, Callable[[], None]]]:
        actions: list[tuple[str, str, Callable[[], None]]] = []
        if package_installed(SNAPSHOT_PACKAGE):
            actions.append(
                (
                    _("Btrfs Snapshots"),
                    _("Create, browse, and roll back system snapshots"),
                    lambda: self._launch(["anduinos-btrfs-snapshots-manager"]),
                )
            )
        actions.append(
            (
                _("Back Up Home Folder"),
                (
                    _("Open Deja Dup backups")
                    if deja_dup_installed
                    else _("Install Deja Dup from the app store")
                ),
                self._open_deja_dup,
            )
        )
        return actions

    def _append_category(
        self,
        title: str,
        icon_name: str,
        actions: list[tuple[str, str, Callable[[], None]]],
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
        for action_title, subtitle, callback in actions:
            body.append(self._action_button(action_title, subtitle, callback))
            search_parts.extend((action_title, subtitle))
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
        button.set_tooltip_text(subtitle)
        button.connect("clicked", lambda _button: callback())
        name = Gtk.Label(label=title, xalign=0)
        name.add_css_class("control-action-title")
        button.set_child(name)
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
            Gio.Subprocess.new(arguments, Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            self._show_error(_("Could not open this setting"), str(error))

    def _show_error(self, heading: str, body: str = "") -> None:
        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("ok", _("OK"))
        dialog.present()

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
        self._show_store_prompt(_("Bottles"), f"{BOTTLES_APP_ID}.desktop")

    def _open_deja_dup(self) -> None:
        if flatpak_installed(DEJA_DUP_APP_ID):
            self._launch(["flatpak", "run", DEJA_DUP_APP_ID])
            return
        self._show_store_prompt(_("Deja Dup Backups"), f"{DEJA_DUP_APP_ID}.desktop")

    def _open_flatseal(self) -> None:
        if package_installed("flatseal"):
            self._launch(["com.github.tchx84.Flatseal"])
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Install Flatseal?"),
            body=_(
                "Flatseal manages permissions for Flatpak applications. "
                "Administrator authentication is required to install it."
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("install", _("Install"))
        dialog.set_close_response("cancel")
        dialog.set_default_response("install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _dialog, response: self._run_package_change(
                "flatseal", self._flatseal_installed
            )
            if response == "install"
            else None,
        )
        dialog.present()

    def _flatseal_installed(self) -> None:
        self._rebuild_categories()
        self._launch(["com.github.tchx84.Flatseal"])

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
        buffer.set_text(
            _("Preparing package operation…")
            + f"\n$ apt-get install --yes {package}\n\n"
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
            status_label.set_label(
                _("✓ On-device AI is ready.")
                if enabled
                else _("✓ On-device AI is disabled.")
            )
            self._append_ai_output(
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
            self._append_ai_output(
                buffer,
                output,
                "\n" + _("✗ Operation failed: ") + message + "\n",
            )
            apply.set_label(_("Retry"))
            apply.set_sensitive(True)
            cancel.set_sensitive(True)

        self._run_ai_package_change(package, buffer, output, completed, failed)

    @staticmethod
    def _append_ai_output(
        buffer: Gtk.TextBuffer, output: Gtk.TextView, text: str
    ) -> bool:
        buffer.insert(buffer.get_end_iter(), text)
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        output.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        return GLib.SOURCE_REMOVE

    def _run_ai_package_change(
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

        def worker() -> None:
            try:
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
                            self._append_ai_output, buffer, output, line
                        )
                    process.stdout.close()
                return_code = process.wait()
                if return_code == 0:
                    GLib.idle_add(success)
                else:
                    GLib.idle_add(
                        failure,
                        _("apt-get exited with status %d") % return_code,
                    )
            except Exception as error:
                GLib.idle_add(failure, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _run_package_change(
        self,
        package: str,
        success: Callable[[], None],
        failure: Callable[[], None] | None = None,
    ) -> None:
        arguments = [
            "/usr/bin/pkexec",
            "/usr/bin/apt-get",
            "install",
            "--yes",
            package,
        ]
        try:
            process = Gio.Subprocess.new(
                arguments,
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
            process.wait_check_async(
                None,
                self._package_change_done,
                (success, failure),
            )
        except GLib.Error as error:
            if failure:
                failure()
            self._show_error(_("Installation failed"), str(error))

    def _package_change_done(self, process, result, callbacks) -> None:
        success, failure = callbacks
        try:
            process.wait_check_finish(result)
        except GLib.Error as error:
            if failure:
                failure()
            self._show_error(_("Installation failed"), str(error))
            return
        success()


class ControlPanelApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._show_about)
        self.add_action(about)

    def do_activate(self) -> None:
        window = self.get_active_window() or ControlPanelWindow(self)
        window.present()

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
