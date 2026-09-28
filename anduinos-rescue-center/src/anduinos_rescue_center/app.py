"""GTK4 interface for selecting an offline AnduinOS installation."""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .client import inspect_target as inspect_rescue_target
from .client import export_file as export_rescue_file
from .client import list_files as list_rescue_files
from .client import create_snapshot as create_rescue_snapshot
from .client import diagnose_boot as diagnose_rescue_boot
from .client import list_snapshots as list_rescue_snapshots
from .client import open_emergency_terminal
from .client import open_live_terminal
from .client import probe
from .client import repair_boot as repair_rescue_boot
from .client import reset_password as reset_rescue_password
from .client import restore_snapshot as restore_rescue_snapshot
from .live import is_live_environment
from .selection import can_open_partition
from .i18n import _


def _size(value: object) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return _("Unknown size")
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1000 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return _("Unknown size")


def _text(value: str, style: str | None = None) -> Gtk.Label:
    label = Gtk.Label(label=value, xalign=0)
    label.set_wrap(True)
    if style:
        label.add_css_class(style)
    return label


def _icon(name: str, size: int = 24) -> Gtk.Image:
    image = Gtk.Image.new_from_icon_name(name)
    image.set_pixel_size(size)
    return image


def _brand(size: int = 88) -> Gtk.Widget:
    filename = "com.anduinos.RescueCenter.svg"
    for path in (
        Path("/usr/share/icons/hicolor/scalable/apps") / filename,
        Path(__file__).resolve().parents[2] / "data" / filename,
    ):
        if path.is_file():
            picture = Gtk.Picture.new_for_filename(str(path))
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(size, size)
            picture.set_valign(Gtk.Align.CENTER)
            return picture
    return _icon("applications-system-symbolic", size)


def _scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(child)
    return scroll


def _install_style(display) -> None:
    css = Gtk.CssProvider()
    css.load_from_data(b"""
        .rescue-hero { background: alpha(@accent_bg_color, .13); border: 1px solid alpha(@accent_color, .24); border-radius: 22px; padding: 28px; }
        .rescue-card { background: @card_bg_color; border: 1px solid alpha(@window_fg_color, .10); border-radius: 18px; padding: 20px; }
        .rescue-card:hover { border-color: alpha(@accent_color, .6); background: alpha(@accent_bg_color, .08); }
        .rescue-mark { color: @accent_color; }
        .rescue-kicker { color: @accent_color; font-weight: 700; letter-spacing: 1px; }
        .rescue-chip { background: alpha(@accent_bg_color, .16); color: @accent_color; border-radius: 999px; padding: 5px 10px; font-weight: 600; }
        .rescue-soft-chip { background: alpha(@window_fg_color, .08); border-radius: 999px; padding: 5px 10px; }
        .navigation-sidebar > row:selected { font-weight: 400; }
    """)
    Gtk.StyleContext.add_provider_for_display(
        display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


class RescueWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application):
        super().__init__(application=application)
        self.set_title(_("AnduinOS Rescue Center"))
        self.set_default_size(1080, 700)
        _install_style(self.get_display())
        self._continued = is_live_environment()
        self._target_payload: dict = {}

        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.header.set_title_widget(Adw.WindowTitle.new(_("Rescue Center"), _("Offline recovery")))
        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=_("Back"))
        self.back.set_visible(False)
        self.back.connect("clicked", lambda *_: self.scan())
        self.header.pack_start(self.back)
        self.refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Scan again"))
        self.refresh.connect("clicked", lambda *_: self.scan())
        self.header.pack_end(self.refresh)
        self.toolbar.add_top_bar(self.header)
        self.set_content(self.toolbar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toolbar.set_content(self.stack)
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.content.set_margin_top(28)
        self.content.set_margin_bottom(40)
        self.content.set_margin_start(36)
        self.content.set_margin_end(36)
        self.content.set_vexpand(True)
        selector = Adw.Clamp(maximum_size=1080)
        selector.set_child(self.content)
        selector.set_vexpand(True)
        self.stack.add_named(_scrolled(selector), "content")

        self.split = Adw.OverlaySplitView()
        self.split.set_min_sidebar_width(238)
        self.split.set_max_sidebar_width(280)
        self.split.set_sidebar_width_fraction(0.26)
        self._build_workspace()
        self.stack.add_named(self.split, "workspace")
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760px"))
        compact.add_setter(self.split, "collapsed", True)
        compact.add_setter(self.split, "show-sidebar", False)
        self.add_breakpoint(compact)

        if self._continued:
            self.scan()
        else:
            self._show_installed_warning()

    def _show_stage(self, name: str) -> None:
        self.header.set_visible(name != "workspace")
        self.stack.set_visible_child_name(name)

    def _build_workspace(self) -> None:
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_header.set_title_widget(Adw.WindowTitle.new(_("Rescue Center"), "ANDUINOS"))
        sidebar_toolbar.add_top_bar(sidebar_header)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        sidebar.set_margin_top(18)
        sidebar.set_margin_bottom(18)
        sidebar.set_margin_start(14)
        sidebar.set_margin_end(14)
        self.target_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.target_card.set_margin_start(12)
        self.target_card.set_margin_end(12)
        sidebar.append(self.target_card)
        self.navigation = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.navigation.add_css_class("navigation-sidebar")
        self.navigation.connect("row-selected", self._navigation_selected)
        sidebar.append(self.navigation)
        sidebar.append(Gtk.Box(vexpand=True))
        change = Gtk.Button(label=_("Choose another system"))
        change.add_css_class("flat")
        change.set_halign(Gtk.Align.START)
        change.connect("clicked", lambda *_: self.scan())
        sidebar.append(change)
        sidebar_toolbar.set_content(_scrolled(sidebar))
        self.split.set_sidebar(sidebar_toolbar)

        content_toolbar = Adw.ToolbarView()
        content_header = Adw.HeaderBar()
        self.page_title = Adw.WindowTitle.new(_("Home"), _("Offline AnduinOS"))
        content_header.set_title_widget(self.page_title)
        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self.sidebar_toggle.set_tooltip_text(_("Show recovery tools"))
        self.sidebar_toggle.connect("toggled", lambda button: self.split.set_show_sidebar(button.get_active()))
        content_header.pack_start(self.sidebar_toggle)
        content_toolbar.add_top_bar(content_header)
        self.workspace_pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        content_toolbar.set_content(self.workspace_pages)
        self.split.set_content(content_toolbar)
        self.split.connect("notify::collapsed", self._sync_sidebar_toggle)
        self.split.connect("notify::show-sidebar", self._sync_sidebar_toggle)
        self._sync_sidebar_toggle()

    def _sync_sidebar_toggle(self, *_args) -> None:
        self.sidebar_toggle.set_visible(self.split.get_collapsed())
        if self.sidebar_toggle.get_active() != self.split.get_show_sidebar():
            self.sidebar_toggle.set_active(self.split.get_show_sidebar())

    def _show_installed_warning(self) -> None:
        self.status.set_icon_name("dialog-warning-symbolic")
        self.status.set_title(_("This is not a Live session"))
        self.status.set_description(
            _("Rescue Center is designed for the AnduinOS Live environment. "
            "Continuing here is not recommended. The currently running system "
            "will remain unavailable as a repair target.")
        )
        button = Gtk.Button(label=_("Continue anyway"))
        button.add_css_class("destructive-action")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", self._continue_installed)
        self.status.set_child(button)
        self._show_stage("status")

    def _continue_installed(self, _button: Gtk.Button) -> None:
        self._continued = True
        self.status.set_child(None)
        self.scan()

    def scan(self) -> None:
        if not self._continued:
            return
        self.refresh.set_sensitive(False)
        self.back.set_visible(False)
        self.status.set_icon_name("drive-harddisk-symbolic")
        self.status.set_title(_("Looking for AnduinOS installations"))
        self.status.set_description(_("Inspecting disks without changing their filesystems…"))
        self._show_stage("status")

        def worker() -> None:
            try:
                payload = probe()
            except Exception as error:
                GLib.idle_add(self._scan_failed, str(error))
                return
            GLib.idle_add(self._render, payload)

        threading.Thread(target=worker, daemon=True).start()

    def _scan_failed(self, message: str) -> bool:
        self.refresh.set_sensitive(True)
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title(_("Could not scan this computer"))
        self.status.set_description(_(message))
        return GLib.SOURCE_REMOVE

    def _clear_content(self) -> None:
        while child := self.content.get_first_child():
            self.content.remove(child)

    def _render(self, payload: dict) -> bool:
        self.refresh.set_sensitive(True)
        self._clear_content()
        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=28)
        hero.add_css_class("rescue-hero")
        hero_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero_text.set_hexpand(True)
        hero_text.append(_text(_("START YOUR RECOVERY"), "rescue-kicker"))
        hero_text.append(_text(_("Find the system you want to repair"), "title-1"))
        hero_text.append(_text(
            _("Choose a detected AnduinOS installation below. We inspect disks read-only "
            "and verify the selected device again before opening it."), "dim-label"
        ))
        hero.append(hero_text)
        artwork = _brand(92)
        artwork.set_valign(Gtk.Align.CENTER)
        hero.append(artwork)
        self.content.append(hero)

        disks = [disk for disk in payload.get("disks", []) if isinstance(disk, dict)]
        installations = [
            (disk, part) for disk in disks
            for part in disk.get("partitions", [])
            if isinstance(part, dict) and part.get("os_kind") == "anduinos"
        ]
        pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        pages.set_vhomogeneous(False)
        pages.set_vexpand(True)
        self.content.append(pages)

        quick = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        quick.set_vexpand(True)
        advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        pages.add_named(quick, "quick")
        pages.add_named(advanced, "advanced")

        back_to_results = Gtk.Button(label=_("Back to detected systems"))
        back_to_results.add_css_class("flat")
        back_to_results.set_halign(Gtk.Align.START)
        back_to_results.connect("clicked", lambda *_: pages.set_visible_child_name("quick"))
        advanced.append(back_to_results)
        advanced.append(_text(_("All detected disks and partitions"), "title-3"))
        advanced.append(_text(_("For systems not found automatically. The selected partition is checked again before repair."), "dim-label"))
        for disk in disks:
            self._append_disk(advanced, disk)
        if installations:
            for disk, partition in installations:
                quick.append(self._installation_row(disk, partition))
        else:
            empty = Adw.StatusPage(
                icon_name="system-search-symbolic",
                title=_("No AnduinOS installation found"),
                description=_("Use Advanced selection to inspect the disks and partitions on this computer."),
            )
            empty.set_vexpand(True)
            quick.append(empty)
        quick.append(Gtk.Box(vexpand=True))
        advanced_selection = Gtk.Button(label=_("Advanced selection"))
        advanced_selection.add_css_class("pill")
        advanced_selection.set_halign(Gtk.Align.CENTER)
        advanced_selection.connect("clicked", lambda *_: pages.set_visible_child_name("advanced"))
        quick.append(advanced_selection)
        self._show_stage("content")
        return GLib.SOURCE_REMOVE

    def _installation_row(self, disk: dict, partition: dict) -> Gtk.Widget:
        button = Gtk.Button()
        button.add_css_class("rescue-card")
        button.set_sensitive(can_open_partition(disk, partition))
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Open {system} installation on {partition}').format(
                system=partition.get('os_name') or 'AnduinOS',
                partition=partition.get('path') or _('unknown partition'))],
        )
        button.connect("clicked", lambda _button, item=partition: self._open_system(item))
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        symbol = _icon("drive-harddisk-system-symbolic", 36)
        symbol.add_css_class("rescue-mark")
        body.append(symbol)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        details.set_hexpand(True)
        details.append(_text(str(partition.get("os_name") or "AnduinOS"), "title-3"))
        details.append(_text(" · ".join(value for value in (
            str(partition.get("hostname") or ""),
            str(disk.get("model") or disk.get("path") or ""),
        ) if value), "dim-label"))
        metadata = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for value in (str(partition.get("filesystem") or _("Unknown")).upper(),
                      str(partition.get("path") or ""), _size(partition.get("size_bytes"))):
            metadata.append(_text(value, "rescue-soft-chip"))
        if not button.get_sensitive():
            reason = (
                _("Running system") if partition.get("active_system") else
                _("Live installation media") if disk.get("live_medium") else
                _("Unavailable for recovery")
            )
            metadata.append(_text(reason, "rescue-soft-chip"))
        details.append(metadata)
        body.append(details)
        body.append(_icon("go-next-symbolic", 20))
        button.set_child(body)
        return button

    def _append_disk(self, container: Gtk.Box, disk: dict) -> None:
        is_live_medium = bool(disk.get("live_medium"))
        group = Adw.PreferencesGroup(
            title=str(disk.get("model") or disk.get("path") or _("Disk")),
            description=" · ".join(
                value for value in (
                    str(disk.get("path") or ""),
                    _size(disk.get("size_bytes")),
                    _("Live installation media") if is_live_medium else "",
                ) if value
            ),
        )
        container.append(group)
        for partition in disk.get("partitions", []):
            if not isinstance(partition, dict):
                continue
            kind = str(partition.get("os_name") or "")
            if not kind and partition.get("os_kind") == "windows":
                kind = "Windows"
            if not kind:
                kind = str(partition.get("filesystem") or _("Unknown filesystem")).upper()
            details = f"{partition.get('path', '')} · {_size(partition.get('size_bytes'))}"
            if partition.get("probe_error"):
                details += " · " + _("Could not inspect")
            row = Adw.ActionRow(title=kind, subtitle=details)
            icon = "drive-harddisk-system-symbolic" if partition.get("os_kind") == "anduinos" else "drive-harddisk-symbolic"
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            selectable = can_open_partition(disk, partition)
            if selectable:
                row.set_activatable(True)
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", lambda _row, item=partition: self._open_system(item))
            elif partition.get("active_system"):
                row.set_subtitle(details + " · " + _("Running system"))
            elif is_live_medium:
                row.set_subtitle(details + " · " + _("Live installation media"))
            elif partition.get("os_kind") == "windows":
                row.set_subtitle(details + " · " + _("Windows cannot be repaired here"))
            else:
                row.set_subtitle(details + " · " + _("Not available for recovery"))
            group.add(row)

    def _open_system(self, partition: dict) -> None:
        path = str(partition.get("path") or "")
        identity = str(partition.get("identity") or "")
        self.refresh.set_sensitive(False)
        self.status.set_icon_name("drive-harddisk-system-symbolic")
        self.status.set_title(_("Inspecting the selected system"))
        self.status.set_description(_("Re-checking its disk identity and reading system information…"))
        self._show_stage("status")

        def worker() -> None:
            try:
                payload = inspect_rescue_target(path, identity)
            except Exception as error:
                GLib.idle_add(self._open_failed, str(error))
                return
            GLib.idle_add(self._render_target, payload)

        threading.Thread(target=worker, daemon=True).start()

    def _open_failed(self, message: str) -> bool:
        self.refresh.set_sensitive(True)
        self.back.set_visible(False)
        self._show_stage("content")
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Could not open this installation"),
            body=_(message),
        )
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present()
        return GLib.SOURCE_REMOVE

    def _target_failed(self, message: str) -> bool:
        self.refresh.set_sensitive(True)
        self.back.set_visible(True)
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title(_("Could not open this installation"))
        self.status.set_description(_(message))
        self._show_stage("status")
        return GLib.SOURCE_REMOVE

    def _render_target(self, payload: dict) -> bool:
        self.refresh.set_sensitive(True)
        system = payload.get("system") if isinstance(payload.get("system"), dict) else {}
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        self._target_payload = payload
        self._clear_box(self.target_card)
        self.target_card.append(_text(_("SELECTED INSTALLATION"), "rescue-kicker"))
        self.target_card.append(_text(str(system.get("name") or "AnduinOS"), "title-3"))
        self.target_card.append(_text(
            " · ".join(value for value in (
                str(system.get("hostname") or ""), str(target.get("path") or "")
            ) if value), "dim-label"
        ))
        self._clear_listbox(self.navigation)
        while child := self.workspace_pages.get_first_child():
            self.workspace_pages.remove(child)
        self._nav_rows = {}
        navigation_items = [
            ("home", _("Home"), "user-home-symbolic"),
            ("passwords", _("Password reset"), "dialog-password-symbolic"),
            ("files", _("File browser"), "folder-open-symbolic"),
        ]
        if system.get("btrfs_layout"):
            navigation_items.append(
                ("snapshots", _("Snapshots & restore"), "view-restore-symbolic")
            )
        navigation_items.extend((
            ("boot", _("Boot repair"), "system-run-symbolic"),
            ("terminal", _("Emergency terminal"), "utilities-terminal-symbolic"),
        ))
        navigation_items.append(("details", _("System details"), "computer-symbolic"))
        for name, title, icon in navigation_items:
            row = Gtk.ListBoxRow()
            row.update_property(
                [Gtk.AccessibleProperty.LABEL], [_('Open {page} page').format(page=title)]
            )
            row.set_child(self._navigation_item(title, icon))
            self.navigation.append(row)
            self._nav_rows[name] = row

        self.workspace_pages.add_named(self._wrap_page(self._home_page(payload)), "home")
        self.workspace_pages.add_named(self._wrap_page(self._password_page(payload)), "passwords")
        self.file_page = FileBrowserPage(self, target)
        self.workspace_pages.add_named(self.file_page, "files")
        self.snapshot_page = None
        if system.get("btrfs_layout"):
            self.snapshot_page = SnapshotPage(self, target)
            self.workspace_pages.add_named(self.snapshot_page, "snapshots")
        self.boot_page = BootRepairPage(self, target)
        self.workspace_pages.add_named(self.boot_page, "boot")
        self.workspace_pages.add_named(self._wrap_page(self._terminal_page(target)), "terminal")
        self.workspace_pages.add_named(self._wrap_page(self._details_page(payload)), "details")
        self.navigation.select_row(self._nav_rows["home"])
        self._show_stage("workspace")
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _clear_box(box: Gtk.Box) -> None:
        while child := box.get_first_child():
            box.remove(child)

    @staticmethod
    def _clear_listbox(box: Gtk.ListBox) -> None:
        while child := box.get_first_child():
            box.remove(child)

    @staticmethod
    def _navigation_item(title: str, icon: str) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        body.set_margin_top(10)
        body.set_margin_bottom(10)
        body.set_margin_start(12)
        body.set_margin_end(12)
        # Normalize visible glyphs rather than SVG viewports: these themed
        # assets have different amounts of internal padding.
        image = _icon(icon, {
            "user-home-symbolic": 21,
            "view-restore-symbolic": 21,
            "system-run-symbolic": 25,
        }.get(icon, 19))
        image.set_size_request(26, 26)
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        body.append(image)
        body.append(_text(title))
        return body

    def _navigation_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None or not hasattr(self, "_nav_rows"):
            return
        name = next((key for key, value in self._nav_rows.items() if value == row), None)
        if name is None:
            return
        self.workspace_pages.set_visible_child_name(name)
        self.page_title.set_title({
            "home": _("Home"), "passwords": _("Password reset"), "files": _("File browser"),
            "snapshots": _("Snapshots & restore"), "boot": _("Boot repair"),
            "terminal": _("Emergency terminal"), "details": _("System details"),
        }[name])
        if name == "files" and not self.file_page.loaded:
            self.file_page.load(".")
        if name == "snapshots" and self.snapshot_page and not self.snapshot_page.loaded:
            self.snapshot_page.load()
        if name == "boot" and not self.boot_page.loaded:
            self.boot_page.load()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def _navigate(self, name: str) -> None:
        self.navigation.select_row(self._nav_rows[name])

    @staticmethod
    def _wrap_page(page: Gtk.Widget) -> Gtk.Widget:
        clamp = Adw.Clamp(maximum_size=900)
        clamp.set_child(page)
        return _scrolled(clamp)

    @staticmethod
    def _page_intro(kicker: str, title: str, description: str) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(32)
        page.set_margin_bottom(40)
        page.set_margin_start(30)
        page.set_margin_end(30)
        page.append(_text(kicker, "rescue-kicker"))
        page.append(_text(title, "title-1"))
        page.append(_text(description, "dim-label"))
        return page

    def _home_page(self, payload: dict) -> Gtk.Widget:
        system = payload.get("system", {})
        target = payload.get("target", {})
        page = self._page_intro(
            _("RECOVERY WORKSPACE"), _("A calmer way back"),
            _("Work on this offline installation. Choose a tool on the left; your running system remains untouched.")
        )
        banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=22)
        banner.add_css_class("rescue-hero")
        banner_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        banner_text.set_hexpand(True)
        banner_text.append(_text(str(system.get("name") or "AnduinOS"), "title-1"))
        banner_text.append(_text(" · ".join(value for value in (
            str(system.get("version") or ""), str(system.get("hostname") or ""),
        ) if value), "dim-label"))
        target_chip = _text(_('Offline installation · {partition}').format(
            partition=target.get("path") or _("Unknown partition")), "rescue-chip")
        target_chip.set_halign(Gtk.Align.START)
        banner_text.append(target_chip)
        banner.append(banner_text)
        banner.append(_brand(88))
        page.append(banner)
        page.append(_text(_("Recovery tools"), "title-2"))
        tools = [
            ("passwords", _("Reset a password"), _("Restore access to a local account."), "dialog-password-symbolic"),
            ("files", _("Browse and copy files"), _("Explore the offline system and copy data out."), "folder-open-symbolic"),
        ]
        if system.get("btrfs_layout"):
            tools.append((
                "snapshots", _("Snapshots & restore"),
                _("Create a recovery point or roll back the system."),
                "view-restore-symbolic",
            ))
        tools.extend((
            ("boot", _("Diagnose and repair boot"), _("Check the installed kernel, GRUB and EFI boot path."), "system-run-symbolic"),
            ("terminal", _("Emergency terminal"), _("Open an expert shell in this offline system."), "utilities-terminal-symbolic"),
        ))
        tools.append((
            "details", _("System details"), _("Review the installation before making changes."),
            "computer-symbolic",
        ))
        for name, title, description, icon in tools:
            button = Gtk.Button()
            button.add_css_class("rescue-card")
            button.connect("clicked", lambda _button, page_name=name: self._navigate(page_name))
            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            symbol = _icon(icon, 30)
            symbol.add_css_class("rescue-mark")
            body.append(symbol)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            words.set_hexpand(True)
            words.append(_text(title, "title-4"))
            words.append(_text(description, "dim-label"))
            body.append(words)
            body.append(_icon("go-next-symbolic", 18))
            button.set_child(body)
            page.append(button)
        if not system.get("btrfs_layout"):
            page.append(_text(
                _("Snapshot restore is available only for the standard AnduinOS Btrfs layout. "
                "File recovery and password reset remain available here."), "dim-label"
            ))
        return page

    def _terminal_page(self, target: dict) -> Gtk.Widget:
        page = self._page_intro(
            _("ADVANCED RECOVERY"), _("Emergency terminal"),
            _("Open a root shell inside the selected offline system. Commands can change or delete its files.")
        )
        page.append(_text(
            _("The terminal will show the target partition before entering it. "
            "Type exit when finished. Recovery will unmount its temporary filesystems and report any cleanup failure."),
            "dim-label",
        ))
        button = Gtk.Button(label=_("Open target terminal"))
        button.add_css_class("suggested-action")
        button.set_halign(Gtk.Align.START)
        button.connect("clicked", lambda *_: self._terminal_dialog(target))
        page.append(button)
        fallback = Gtk.Button(label=_("Open Live terminal instead"))
        fallback.add_css_class("flat")
        fallback.set_halign(Gtk.Align.START)
        fallback.connect("clicked", lambda *_: self._open_live_terminal())
        page.append(fallback)
        return page

    def _open_live_terminal(self) -> None:
        try:
            open_live_terminal()
        except Exception as error:
            failed = Adw.MessageDialog(transient_for=self, heading=_("Could not open terminal"), body=_(str(error)))
            failed.add_response("close", _("Close"))
            failed.set_close_response("close")
            failed.present()

    def _terminal_dialog(self, target: dict) -> None:
        path = str(target.get("path") or "")
        identity = str(target.get("identity") or "")
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Open an expert root shell?"),
            body=_("Target: {path}\n\nThe shell can make unrestricted changes to this offline "
                   "installation and can also access Live hardware. Use it only if you understand the commands.").format(path=path),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("open", _("Open terminal"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def responded(_dialog, response: str) -> None:
            if response != "open":
                return
            try:
                open_emergency_terminal(path, identity)
            except Exception as error:
                failed = Adw.MessageDialog(transient_for=self, heading=_("Could not open terminal"), body=_(str(error)))
                failed.add_response("close", _("Close"))
                failed.set_close_response("close")
                failed.present()

        dialog.connect("response", responded)
        dialog.present()

    def _password_page(self, payload: dict) -> Gtk.Widget:
        page = self._page_intro(
            _("ACCOUNT ACCESS"), _("Reset a local password"),
            _("Choose an account on the offline installation. Existing encrypted login keyrings are not unlocked by a password reset.")
        )
        users = Adw.PreferencesGroup(title=_("Local accounts"))
        page.append(users)
        found = False
        for user in payload.get("users", []):
            if not isinstance(user, dict):
                continue
            found = True
            name = str(user.get("display_name") or user.get("name") or _("Unknown"))
            subtitle = f"{user.get('name', '')} · {user.get('home', '')}"
            if user.get("locked") is True:
                subtitle += " · " + _("Password locked")
            row = Adw.ActionRow(title=name, subtitle=subtitle)
            row.add_prefix(_icon("avatar-default-symbolic"))
            button = Gtk.Button(label=_("Reset password"))
            button.set_valign(Gtk.Align.CENTER)
            button.connect("clicked", lambda _button, username=str(user.get("name") or ""): self._password_dialog(username))
            row.add_suffix(button)
            users.add(row)
        if not found:
            users.add(Adw.ActionRow(title=_("No local accounts found")))
        return page

    def _details_page(self, payload: dict) -> Gtk.Widget:
        system = payload.get("system", {})
        target = payload.get("target", {})
        page = self._page_intro(
            _("INSTALLATION DETAILS"), _("Know what you are repairing"),
            _("Information read from the selected offline installation. Its device identity is rechecked before each operation.")
        )
        info = Adw.PreferencesGroup(title=_("System"))
        page.append(info)
        for label, value in (
            (_("Operating system"), system.get("name") or "AnduinOS"),
            (_("Version"), system.get("version") or _("Unknown")),
            (_("Computer name"), system.get("hostname") or _("Unknown")),
            (_("Filesystem"), str(system.get("filesystem") or _("Unknown")).upper()),
            (_("Partition"), target.get("path") or _("Unknown")),
            (_("Installed kernels"), ", ".join(system.get("kernels") or ()) or _("Unknown")),
            (_("Snapshots"), _("Available") if system.get("btrfs_layout") else _("Requires the standard AnduinOS Btrfs layout")),
        ):
            info.add(Adw.ActionRow(title=label, subtitle=str(value)))
        return page

    def _password_dialog(self, username: str) -> None:
        password = Adw.PasswordEntryRow(title=_("New password"))
        confirmation = Adw.PasswordEntryRow(title=_("Confirm new password"))
        form = Adw.PreferencesGroup()
        form.add(password)
        form.add(confirmation)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_("Reset the password for {username}?").format(username=username),
            body=(
                _("This changes the local login password on the selected offline system. "
                "It does not unlock an existing encrypted login keyring.")
            ),
            extra_child=form,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reset", _("Reset password"))
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("reset")
        dialog.set_close_response("cancel")

        def validate(*_args) -> None:
            value = password.get_text()
            dialog.set_response_enabled(
                "reset", bool(value) and value == confirmation.get_text()
            )

        password.connect("changed", validate)
        confirmation.connect("changed", validate)
        validate()

        def responded(_dialog, response: str) -> None:
            if response != "reset":
                return
            value = password.get_text()
            target = self._target_payload.get("target", {})
            self.status.set_icon_name("dialog-password-symbolic")
            self.status.set_title(_("Resetting the password"))
            self.status.set_description(_("Writing the new local account password safely…"))
            self._show_stage("status")
            self.refresh.set_sensitive(False)

            def worker() -> None:
                try:
                    reset_rescue_password(
                        str(target.get("path") or ""),
                        str(target.get("identity") or ""),
                        username,
                        value,
                    )
                except Exception as error:
                    GLib.idle_add(self._target_failed, str(error))
                    return
                GLib.idle_add(self._password_done, username)

            threading.Thread(target=worker, daemon=True).start()

        dialog.connect("response", responded)
        dialog.present()

    def _password_done(self, username: str) -> bool:
        self.refresh.set_sensitive(True)
        done = Adw.MessageDialog(
            transient_for=self,
            heading=_("Password reset complete"),
            body=_("The local login password for {username} was changed.").format(username=username),
        )
        done.add_response("close", _("Close"))
        done.set_default_response("close")
        done.set_close_response("close")
        done.present()
        target = self._target_payload.get("target", {})
        self._open_system(target)
        return GLib.SOURCE_REMOVE


class FileBrowserPage(Gtk.Box):
    def __init__(self, parent: Gtk.Window, target: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.parent_window = parent
        self.target = target
        self.current = "."
        self.parent_path: str | None = None
        self.loaded = False

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        toolbar.set_margin_top(14)
        toolbar.set_margin_start(30)
        toolbar.set_margin_end(30)
        self.up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Parent folder"))
        self.up.set_sensitive(False)
        self.up.connect("clicked", self._go_up)
        toolbar.append(self.up)
        toolbar.append(_text(_("Offline filesystem"), "dim-label"))
        self.append(toolbar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.append(self.stack)
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.page.set_margin_top(28)
        self.page.set_margin_bottom(28)
        self.page.set_margin_start(30)
        self.page.set_margin_end(30)
        self.page.append(_text(_("FILE RECOVERY"), "rescue-kicker"))
        self.page.append(_text(_("Browse and copy your files"), "title-1"))
        self.page.append(_text(_("Explore this offline installation without modifying its files."), "dim-label"))
        self.location = Gtk.Label(xalign=0)
        self.location.add_css_class("heading")
        self.page.append(self.location)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        scroll.set_child(self.listbox)
        self.page.append(scroll)
        self.stack.add_named(self.page, "files")

    def load(self, relative: str) -> None:
        self.loaded = True
        self.status.set_child(None)
        self.status.set_icon_name("folder-open-symbolic")
        self.status.set_title(_("Opening offline files"))
        self.status.set_description(_("Reading this folder without changing the installed system…"))
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                payload = list_rescue_files(
                    str(self.target.get("path") or ""),
                    str(self.target.get("identity") or ""),
                    relative,
                )
            except Exception as error:
                GLib.idle_add(self._failed, str(error))
                return
            GLib.idle_add(self._render_files, payload)

        threading.Thread(target=worker, daemon=True).start()

    def _render_files(self, payload: dict) -> bool:
        while row := self.listbox.get_first_child():
            self.listbox.remove(row)
        self.current = str(payload.get("path") or ".")
        self.parent_path = payload.get("parent")
        self.up.set_sensitive(isinstance(self.parent_path, str))
        self.location.set_label("/" if self.current == "." else "/" + self.current)
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind") or "other")
            subtitle = {
                "file": _("File"), "directory": _("Folder"), "other": _("Other")
            }.get(kind, kind.capitalize())
            if kind == "file":
                subtitle += " · " + _size(entry.get("size"))
            row = Adw.ActionRow(title=str(entry.get("name") or ""), subtitle=subtitle)
            icon = "folder-symbolic" if kind == "directory" else "text-x-generic-symbolic"
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            if kind in {"directory", "file"}:
                export = Gtk.Button(icon_name="document-save-symbolic", tooltip_text=_("Copy out"))
                export.set_valign(Gtk.Align.CENTER)
                export.connect("clicked", lambda _button, item=entry: self._choose_export(item))
                row.add_suffix(export)
            if kind == "directory":
                row.set_activatable(True)
                row.connect("activated", lambda _row, item=entry: self.load(str(item["path"])))
            self.listbox.append(row)
        if payload.get("truncated"):
            self.listbox.append(
                Adw.ActionRow(
                    title=_("This folder contains more items"),
                    subtitle=_("Only the first 5,000 entries are shown."),
                )
            )
        self.stack.set_visible_child_name("files")
        return GLib.SOURCE_REMOVE

    def _go_up(self, _button: Gtk.Button) -> None:
        if isinstance(self.parent_path, str):
            self.load(self.parent_path)

    def _failed(self, message: str) -> bool:
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title(_("Could not read offline files"))
        self.status.set_description(_(message))
        retry = Gtk.Button(label=_("Try again"))
        retry.add_css_class("pill")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self.load(self.current))
        self.status.set_child(retry)
        self.stack.set_visible_child_name("status")
        return GLib.SOURCE_REMOVE

    def _choose_export(self, entry: dict) -> None:
        chooser = Gtk.FileDialog(title=_("Choose where to copy this item"), modal=True)

        def selected(dialog, result) -> None:
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            destination = folder.get_path()
            if destination:
                self._export(entry, destination)

        chooser.select_folder(self.parent_window, None, selected)

    def _export(self, entry: dict, destination: str) -> None:
        self.status.set_icon_name("document-save-symbolic")
        self.status.set_title(_("Copying rescued data"))
        self.status.set_description(_("Keep the computer powered on until copying finishes."))
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                exported = export_rescue_file(
                    str(self.target.get("path") or ""),
                    str(self.target.get("identity") or ""),
                    str(entry.get("path") or ""),
                    destination,
                )
            except Exception as error:
                GLib.idle_add(self._failed, str(error))
                return
            GLib.idle_add(self._exported, exported)

        threading.Thread(target=worker, daemon=True).start()

    def _exported(self, path: str) -> bool:
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Copy complete"),
            body=_("The rescued item was copied to:\n{path}").format(path=path),
        )
        dialog.add_response("close", _("Close"))
        dialog.present()
        self.load(self.current)
        return GLib.SOURCE_REMOVE


class BootRepairPage(Gtk.Box):
    def __init__(self, parent: Gtk.Window, target: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.parent_window = parent
        self.target = target
        self.loaded = False
        self.report: dict = {}
        self._repair_active = False
        self._output_lines: list[str] = []

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        toolbar.set_margin_top(14)
        toolbar.set_margin_start(30)
        toolbar.set_margin_end(30)
        title = _text(_("BOOT RECOVERY"), "rescue-kicker")
        title.set_hexpand(True)
        toolbar.append(title)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Check boot again"))
        refresh.connect("clicked", lambda *_: self.load())
        toolbar.append(refresh)
        self.refresh_button = refresh
        self.append(toolbar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.append(self.stack)
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")
        page = RescueWindow._page_intro(
            _("BOOT RECOVERY"), _("Diagnose and repair boot"),
            _("Inspect this offline installation before making changes. Repair is offered only when its EFI partition can be identified on the selected disk."),
        )
        self.details = Adw.PreferencesGroup(title=_("Boot path"))
        page.append(self.details)
        self.findings = Adw.PreferencesGroup(title=_("Findings"))
        page.append(self.findings)
        self._detail_rows: list[Adw.ActionRow] = []
        self._finding_rows: list[Adw.ActionRow] = []
        self.repair_button = Gtk.Button(label=_("Rebuild AnduinOS boot"))
        self.repair_button.add_css_class("suggested-action")
        self.repair_button.set_halign(Gtk.Align.START)
        self.repair_button.connect("clicked", self._repair_dialog)
        page.append(self.repair_button)
        self.report_output = Gtk.Expander(label=_("View last repair output"))
        self.report_output.set_visible(False)
        report_scroll = Gtk.ScrolledWindow(min_content_height=180)
        self.report_log = Gtk.TextView(
            editable=False, cursor_visible=False, monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        report_scroll.set_child(self.report_log)
        self.report_output.set_child(report_scroll)
        page.append(self.report_output)
        clamp = Adw.Clamp(maximum_size=900)
        clamp.set_child(page)
        self.stack.add_named(_scrolled(clamp), "report")

    def _identity(self) -> tuple[str, str]:
        return str(self.target.get("path") or ""), str(self.target.get("identity") or "")

    def load(self) -> None:
        self.loaded = True
        self.status.set_child(None)
        self.status.set_icon_name("system-run-symbolic")
        self.status.set_title(_("Checking the offline boot path"))
        self.status.set_description(_("Reading the installed kernel, GRUB configuration, EFI files and firmware entry without changing them…"))
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                report = diagnose_rescue_boot(*self._identity())
            except Exception as error:
                GLib.idle_add(self._failed, str(error))
                return
            GLib.idle_add(self._render, report)

        threading.Thread(target=worker, daemon=True).start()

    def _render(self, report: dict) -> bool:
        self.report = report
        for group, rows in ((self.details, self._detail_rows), (self.findings, self._finding_rows)):
            for row in rows:
                group.remove(row)
            rows.clear()
        for title, value in (
            (_("Selected installation"), report.get("target") or _("Unknown")),
            (_("EFI partition"), report.get("esp") or _("Not identified")),
            (_("Kernel and initrd pairs"), _("{complete} of {total}").format(
                complete=report.get("complete_pairs", 0), total=report.get("kernel_count", 0))),
            (_("GRUB kernel entry"), _("Found") if report.get("grub_config") else _("Missing or incomplete")),
            (_("AnduinOS EFI loader"), _("Present") if report.get("efi_loader") else _("Missing")),
            (_("Firmware boot entry"), {
                True: _("Found"), False: _("Missing"), None: _("Unavailable")
            }.get(report.get("nvram_entry"), _("Unknown"))),
        ):
            row = Adw.ActionRow(title=title, subtitle=str(value))
            self.details.add(row)
            self._detail_rows.append(row)
        issues = report.get("issues") or []
        for issue in issues:
            row = Adw.ActionRow(title=_(str(issue)))
            self.findings.add(row)
            self._finding_rows.append(row)
        if not issues:
            row = Adw.ActionRow(title=_("Static boot checks found no missing component. A real reboot is still required."))
            self.findings.add(row)
            self._finding_rows.append(row)
        self.repair_button.set_sensitive(bool(report.get("repairable")))
        self.stack.set_visible_child_name("report")
        return GLib.SOURCE_REMOVE

    def _failed(self, message: str) -> bool:
        if self._repair_active:
            self._progress_line(_("Repair failed: {error}").format(error=message))
            self._repair_active = False
            self.refresh_button.set_sensitive(True)
            self.parent_window.set_deletable(True)
            self.activity_progress.set_visible(False)
            self.activity_phase.set_label(_("Repair stopped. Review the output before trying again."))
            self.status.set_icon_name("dialog-error-symbolic")
            self.status.set_title(_("Boot repair failed"))
            self.status.set_description(_(message))
            self.stack.set_visible_child_name("status")
            return GLib.SOURCE_REMOVE
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title(_("Boot check or repair failed"))
        self.status.set_description(_(message))
        retry = Gtk.Button(label=_("Check again"))
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self.load())
        self.status.set_child(retry)
        self.stack.set_visible_child_name("status")
        return GLib.SOURCE_REMOVE

    def _repair_dialog(self, _button: Gtk.Button) -> None:
        if not self.report.get("repairable"):
            return
        changes = "\n• ".join(_(str(item)) for item in self.report.get("changes") or [])
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Rebuild boot for this installation?"),
            body=_("System: {system}\nEFI partition: {esp}\n\n"
                   "This will:\n• {changes}\n\nOther EFI vendors and the removable EFI/BOOT path will not be changed.").format(
                       system=self.report.get('target'), esp=self.report.get('esp'), changes=changes),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("repair", _("Repair boot"))
        dialog.set_response_appearance("repair", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _dialog, response: self._repair()
                       if response == "repair" else None)
        dialog.present()

    def _repair(self) -> None:
        esp_identity = str(self.report.get("esp_identity") or "")
        self._output_lines = []
        self._repair_active = True
        self.refresh_button.set_sensitive(False)
        self.parent_window.set_deletable(False)
        self._show_repair_activity()
        self.status.set_icon_name("system-run-symbolic")
        self.status.set_title(_("Rebuilding the offline boot path"))
        self.status.set_description(_("Keep the computer powered on. The stage and output below update as work proceeds."))
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                report = repair_rescue_boot(
                    *self._identity(), esp_identity,
                    on_progress=lambda message: GLib.idle_add(self._progress_line, message),
                )
            except Exception as error:
                GLib.idle_add(self._failed, str(error))
                return
            GLib.idle_add(self._repaired, report)

        threading.Thread(target=worker, daemon=True).start()

    def _show_repair_activity(self) -> None:
        activity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        activity.set_size_request(620, -1)
        self.activity_phase = Gtk.Label(label=_("Preparing repair…"), wrap=True)
        activity.append(self.activity_phase)
        self.activity_progress = Gtk.ProgressBar()
        activity.append(self.activity_progress)
        view_button = Gtk.ToggleButton(label=_("View output"))
        view_button.set_halign(Gtk.Align.CENTER)
        activity.append(view_button)
        output_revealer = Gtk.Revealer()
        output_revealer.set_reveal_child(False)
        view_button.connect("toggled", lambda button: output_revealer.set_reveal_child(button.get_active()))
        output_scroll = Gtk.ScrolledWindow(min_content_height=190)
        self.activity_log = Gtk.TextView(
            editable=False, cursor_visible=False, monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        output_scroll.set_child(self.activity_log)
        output_revealer.set_child(output_scroll)
        activity.append(output_revealer)
        self.status.set_child(activity)

        def pulse() -> bool:
            if not self._repair_active:
                return GLib.SOURCE_REMOVE
            self.activity_progress.pulse()
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(120, pulse)

    def _progress_line(self, message: str) -> bool:
        self._output_lines.append(message)
        buffer = self.activity_log.get_buffer()
        if len(self._output_lines) > 500:
            self._output_lines = self._output_lines[-500:]
            buffer.set_text("\n".join(self._output_lines) + "\n")
        else:
            buffer.insert(buffer.get_end_iter(), message + "\n")
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.activity_log.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
        if not message.startswith(("$ ", "  ", "Completed:", "Repair failed:")):
            self.activity_phase.set_label(message)
        return GLib.SOURCE_REMOVE

    def _repaired(self, report: dict) -> bool:
        self._repair_active = False
        self.refresh_button.set_sensitive(True)
        self.parent_window.set_deletable(True)
        self.activity_progress.set_visible(False)
        self.report_log.get_buffer().set_text("\n".join(self._output_lines) + "\n")
        self.report_output.set_visible(True)
        self._render(report)
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Boot repair finished") if not report.get("issues") else _("Boot repair needs review"),
            body=(_("Static boot checks passed. Shut down Live, remove the installation media and test booting.")
                  if not report.get("issues") else _("Some checks still report a problem. Review the findings before rebooting.")),
        )
        dialog.add_response("close", _("Close"))
        dialog.set_close_response("close")
        dialog.present()
        return GLib.SOURCE_REMOVE


class SnapshotPage(Gtk.Box):
    def __init__(self, parent: Gtk.Window, target: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.parent_window = parent
        self.target = target
        self.loaded = False

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        toolbar.set_margin_top(14)
        toolbar.set_margin_start(30)
        toolbar.set_margin_end(30)
        label = _text(_("SYSTEM RECOVERY POINTS"), "rescue-kicker")
        label.set_hexpand(True)
        toolbar.append(label)
        create = Gtk.Button(label=_("Create snapshot"))
        create.add_css_class("suggested-action")
        create.connect("clicked", self._create_dialog)
        toolbar.append(create)
        self.append(toolbar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.append(self.stack)
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")
        scroll = Gtk.ScrolledWindow()
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(28)
        page.set_margin_bottom(28)
        page.set_margin_start(30)
        page.set_margin_end(30)
        page.append(_text(_("SYSTEM RECOVERY"), "rescue-kicker"))
        page.append(_text(_("Snapshots & restore"), "title-1"))
        page.append(_text(_("Create a recovery point or return this offline system to an earlier state. Personal files in Home are not part of system snapshots."), "dim-label"))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        page.append(self.listbox)
        scroll.set_child(page)
        self.stack.add_named(scroll, "snapshots")

    def _target_identity(self) -> tuple[str, str]:
        return str(self.target.get("path") or ""), str(self.target.get("identity") or "")

    def load(self) -> None:
        self.loaded = True
        self.status.set_child(None)
        self.status.set_icon_name("document-open-recent-symbolic")
        self.status.set_title(_("Loading system snapshots"))
        self.status.set_description(
            _("Checking recovery metadata and completing any interrupted restore…")
        )
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                payload = list_rescue_snapshots(*self._target_identity())
            except Exception as error:
                GLib.idle_add(self._failed, str(error))
                return
            GLib.idle_add(self._render, payload)

        threading.Thread(target=worker, daemon=True).start()

    def _render(self, payload: dict) -> bool:
        while row := self.listbox.get_first_child():
            self.listbox.remove(row)
        deployments = payload.get("deployments", [])
        if not deployments:
            self.status.set_icon_name("document-new-symbolic")
            self.status.set_title(_("No system snapshots"))
            self.status.set_description(_("Create a snapshot before making risky system changes."))
            self.stack.set_visible_child_name("status")
            return GLib.SOURCE_REMOVE
        for deployment in deployments:
            if not isinstance(deployment, dict):
                continue
            title = str(deployment.get("title") or _("System snapshot"))
            state = str(deployment.get("state") or "unknown")
            kind = str(deployment.get("kind") or "manual")
            created = str(deployment.get("created_at") or "")
            kinds = {
                "factory": _("Factory"), "manual": _("Manual"),
                "automatic": _("Automatic"), "apt-pre": _("Before update"),
                "apt-post": _("After update"), "pre-rollback": _("Before restore"),
                "imported": _("Imported"),
            }
            states = {
                "creating": _("Creating"), "ready": _("Ready"),
                "incomplete": _("Incomplete"), "broken": _("Broken"),
                "deleting": _("Deleting"),
            }
            row = Adw.ActionRow(
                title=title,
                subtitle=" · ".join(value for value in (
                    created, kinds.get(kind, kind), states.get(state, state)) if value),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))
            if state == "ready" and isinstance(deployment.get("id"), str):
                restore = Gtk.Button(label=_("Restore"))
                restore.set_valign(Gtk.Align.CENTER)
                restore.connect(
                    "clicked",
                    lambda _button, item=deployment: self._restore_dialog(item),
                )
                row.add_suffix(restore)
            else:
                badge = Gtk.Label(label=_("Unavailable"))
                badge.add_css_class("dim-label")
                row.add_suffix(badge)
            self.listbox.append(row)
        self.stack.set_visible_child_name("snapshots")
        return GLib.SOURCE_REMOVE

    def _failed(self, message: str) -> bool:
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title(_("Snapshot operation failed"))
        self.status.set_description(_(message))
        retry = Gtk.Button(label=_("Try again"))
        retry.add_css_class("pill")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self.load())
        self.status.set_child(retry)
        self.stack.set_visible_child_name("status")
        return GLib.SOURCE_REMOVE

    def _create_dialog(self, _button: Gtk.Button) -> None:
        name = Adw.EntryRow(title=_("Snapshot name"))
        form = Adw.PreferencesGroup()
        form.add(name)
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Create a snapshot of the offline system?"),
            body=_("Personal files in Home are not included in system snapshots."),
            extra_child=form,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("create", _("Create"))
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("create", False)
        dialog.set_close_response("cancel")
        name.connect(
            "changed",
            lambda *_: dialog.set_response_enabled("create", bool(name.get_text().strip())),
        )
        dialog.connect(
            "response",
            lambda _dialog, response: self._create(name.get_text())
            if response == "create"
            else None,
        )
        dialog.present()

    def _create(self, title: str) -> None:
        self.status.set_icon_name("document-new-symbolic")
        self.status.set_title(_("Creating system snapshot"))
        self.status.set_description(_("Keep the computer powered on until this finishes."))
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                create_rescue_snapshot(*self._target_identity(), title)
            except Exception as error:
                GLib.idle_add(self._failed, str(error))
                return
            GLib.idle_add(self.load)

        threading.Thread(target=worker, daemon=True).start()

    def _restore_dialog(self, deployment: dict) -> None:
        protect = Gtk.CheckButton(label=_("Create a safety snapshot of the current system first"))
        protect.set_active(True)
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("Restore {snapshot}?").format(
                snapshot=deployment.get('title') or _("this snapshot")),
            body=(
                _("The offline system root will be replaced immediately. Personal files in Home "
                "will not be changed. Do not power off the computer during recovery.")
            ),
            extra_child=protect,
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore system"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: self._restore(
                str(deployment.get("id") or ""), protect.get_active()
            )
            if response == "restore"
            else None,
        )
        dialog.present()

    def _restore(self, identifier: str, protect_current: bool) -> None:
        self.status.set_icon_name("system-reboot-symbolic")
        self.status.set_title(_("Restoring the offline system"))
        self.status.set_description(_("Keep the computer powered on until recovery is complete."))
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                restore_rescue_snapshot(
                    *self._target_identity(), identifier, protect_current
                )
            except Exception as error:
                GLib.idle_add(self._failed, str(error))
                return
            GLib.idle_add(self._restored)

        threading.Thread(target=worker, daemon=True).start()

    def _restored(self) -> bool:
        dialog = Adw.MessageDialog(
            transient_for=self.parent_window,
            heading=_("System restore complete"),
            body=_("Shut down the Live session, remove the installation media, and start AnduinOS."),
        )
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.connect("response", lambda *_: self.load())
        dialog.present()
        return GLib.SOURCE_REMOVE

class RescueApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.anduinos.RescueCenter", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        window = self.props.active_window or RescueWindow(self)
        window.present()


def main() -> int:
    return RescueApplication().run(None)
