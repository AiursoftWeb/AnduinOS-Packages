"""GTK4 interface for selecting an offline AnduinOS installation."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .client import inspect_target as inspect_rescue_target
from .client import export_file as export_rescue_file
from .client import list_files as list_rescue_files
from .client import create_snapshot as create_rescue_snapshot
from .client import list_snapshots as list_rescue_snapshots
from .client import probe
from .client import reset_password as reset_rescue_password
from .client import restore_snapshot as restore_rescue_snapshot
from .live import is_live_environment


def _size(value: object) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "Unknown size"
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1000 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return "Unknown size"


class RescueWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application):
        super().__init__(application=application)
        self.set_title("AnduinOS Rescue Center")
        self.set_default_size(980, 720)
        self._continued = is_live_environment()

        self.toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        self.back.set_visible(False)
        self.back.connect("clicked", lambda *_: self.scan())
        header.pack_start(self.back)
        self.refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Scan again")
        self.refresh.connect("clicked", lambda *_: self.scan())
        header.pack_end(self.refresh)
        self.toolbar.add_top_bar(header)
        self.set_content(self.toolbar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.toolbar.set_content(self.stack)
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.content.set_margin_top(24)
        self.content.set_margin_bottom(24)
        self.content.set_margin_start(32)
        self.content.set_margin_end(32)
        self.stack.add_named(self.content, "content")

        if self._continued:
            self.scan()
        else:
            self._show_installed_warning()

    def _show_installed_warning(self) -> None:
        self.status.set_icon_name("dialog-warning-symbolic")
        self.status.set_title("This is not a Live session")
        self.status.set_description(
            "Rescue Center is designed for the AnduinOS Live environment. "
            "Continuing here is not recommended. The currently running system "
            "will remain unavailable as a repair target."
        )
        button = Gtk.Button(label="Continue anyway")
        button.add_css_class("destructive-action")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", self._continue_installed)
        self.status.set_child(button)
        self.stack.set_visible_child_name("status")

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
        self.status.set_title("Looking for AnduinOS installations")
        self.status.set_description("Inspecting disks without changing their filesystems…")
        self.stack.set_visible_child_name("status")

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
        self.status.set_title("Could not scan this computer")
        self.status.set_description(message)
        return GLib.SOURCE_REMOVE

    def _clear_content(self) -> None:
        while child := self.content.get_first_child():
            self.content.remove(child)

    def _render(self, payload: dict) -> bool:
        self.refresh.set_sensitive(True)
        self._clear_content()
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="Choose an AnduinOS installation", xalign=0)
        title.add_css_class("title-1")
        subtitle = Gtk.Label(
            label="Select a detected system, or inspect every disk in Advanced mode.",
            xalign=0,
        )
        subtitle.add_css_class("dim-label")
        titles.append(title)
        titles.append(subtitle)
        heading.append(titles)
        secure_boot = Gtk.Label(
            label=f"Secure Boot: {str(payload.get('secure_boot', 'unknown')).capitalize()}"
        )
        secure_boot.add_css_class("caption")
        secure_boot.set_halign(Gtk.Align.END)
        secure_boot.set_hexpand(True)
        heading.append(secure_boot)
        self.content.append(heading)

        switcher = Adw.ViewSwitcher()
        pages = Adw.ViewStack()
        switcher.set_stack(pages)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        self.content.append(switcher)
        self.content.append(pages)

        quick = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        quick.set_margin_top(12)
        advanced = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        advanced.set_margin_top(12)
        pages.add_titled_with_icon(quick, "quick", "Quick", "system-search-symbolic")
        pages.add_titled_with_icon(advanced, "advanced", "Advanced", "drive-harddisk-symbolic")

        installations = []
        for disk in payload.get("disks", []):
            if not isinstance(disk, dict):
                continue
            self._append_disk(advanced, disk)
            for partition in disk.get("partitions", []):
                if isinstance(partition, dict) and partition.get("os_kind") == "anduinos":
                    installations.append((disk, partition))
        if installations:
            for disk, partition in installations:
                quick.append(self._installation_row(disk, partition))
        else:
            empty = Adw.StatusPage(
                icon_name="system-search-symbolic",
                title="No AnduinOS installation found",
                description="Open Advanced mode to inspect the detected disks and partitions.",
            )
            empty.set_vexpand(True)
            quick.append(empty)
        self.stack.set_visible_child_name("content")
        return GLib.SOURCE_REMOVE

    def _installation_row(self, disk: dict, partition: dict) -> Gtk.Widget:
        row = Adw.ActionRow(
            title=str(partition.get("os_name") or "AnduinOS"),
            subtitle=" · ".join(
                value for value in (
                    str(partition.get("hostname") or ""),
                    str(disk.get("model") or disk.get("path") or ""),
                    str(partition.get("path") or ""),
                    _size(partition.get("size_bytes")),
                ) if value
            ),
        )
        row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-system-symbolic"))
        if partition.get("active_system"):
            badge = Gtk.Label(label="Running system")
            badge.add_css_class("warning")
            row.add_suffix(badge)
            row.set_sensitive(False)
        else:
            row.set_activatable(True)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda *_: self._open_system(partition))
        return row

    def _append_disk(self, container: Gtk.Box, disk: dict) -> None:
        is_live_medium = bool(disk.get("live_medium"))
        group = Adw.PreferencesGroup(
            title=str(disk.get("model") or disk.get("path") or "Disk"),
            description=" · ".join(
                value for value in (
                    str(disk.get("path") or ""),
                    _size(disk.get("size_bytes")),
                    "Live installation media" if is_live_medium else "",
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
                kind = str(partition.get("filesystem") or "Unknown filesystem").upper()
            details = f"{partition.get('path', '')} · {_size(partition.get('size_bytes'))}"
            if partition.get("probe_error"):
                details += " · Could not inspect"
            row = Adw.ActionRow(title=kind, subtitle=details)
            icon = "drive-harddisk-system-symbolic" if partition.get("os_kind") == "anduinos" else "drive-harddisk-symbolic"
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            selectable = (
                bool(partition.get("path"))
                and bool(partition.get("identity"))
                and not partition.get("active_system")
                and not is_live_medium
            )
            if selectable:
                row.set_activatable(True)
                row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                row.connect("activated", lambda _row, item=partition: self._open_system(item))
            elif partition.get("active_system"):
                row.set_subtitle(details + " · Running system")
            group.add(row)

    def _open_system(self, partition: dict) -> None:
        path = str(partition.get("path") or "")
        identity = str(partition.get("identity") or "")
        self.refresh.set_sensitive(False)
        self.status.set_icon_name("drive-harddisk-system-symbolic")
        self.status.set_title("Inspecting the selected system")
        self.status.set_description("Re-checking its disk identity and reading system information…")
        self.stack.set_visible_child_name("status")

        def worker() -> None:
            try:
                payload = inspect_rescue_target(path, identity)
            except Exception as error:
                GLib.idle_add(self._target_failed, str(error))
                return
            GLib.idle_add(self._render_target, payload)

        threading.Thread(target=worker, daemon=True).start()

    def _target_failed(self, message: str) -> bool:
        self.refresh.set_sensitive(True)
        self.back.set_visible(True)
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title("Could not open this installation")
        self.status.set_description(message)
        return GLib.SOURCE_REMOVE

    def _render_target(self, payload: dict) -> bool:
        self.refresh.set_sensitive(True)
        self.back.set_visible(True)
        self._clear_content()
        system = payload.get("system") if isinstance(payload.get("system"), dict) else {}
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        self._target_payload = payload

        title = Gtk.Label(label=str(system.get("name") or "AnduinOS"), xalign=0)
        title.add_css_class("title-1")
        self.content.append(title)
        subtitle = Gtk.Label(
            label=" · ".join(
                value for value in (
                    str(system.get("hostname") or ""),
                    str(target.get("path") or ""),
                    str(system.get("filesystem") or "").upper(),
                ) if value
            ),
            xalign=0,
        )
        subtitle.add_css_class("dim-label")
        self.content.append(subtitle)

        info = Adw.PreferencesGroup(title="System information")
        self.content.append(info)
        for label, value in (
            ("Version", system.get("version") or "Unknown"),
            ("Computer name", system.get("hostname") or "Unknown"),
            ("Filesystem", str(system.get("filesystem") or "Unknown").upper()),
            ("Installed kernels", ", ".join(system.get("kernels") or ()) or "Unknown"),
        ):
            row = Adw.ActionRow(title=label, subtitle=str(value))
            info.add(row)

        users = Adw.PreferencesGroup(title="Local users")
        self.content.append(users)
        for user in payload.get("users", []):
            if not isinstance(user, dict):
                continue
            name = str(user.get("display_name") or user.get("name") or "Unknown")
            subtitle = f"{user.get('name', '')} · {user.get('home', '')}"
            if user.get("locked") is True:
                subtitle += " · Password locked"
            row = Adw.ActionRow(title=name, subtitle=subtitle)
            row.add_prefix(Gtk.Image.new_from_icon_name("avatar-default-symbolic"))
            button = Gtk.Button(label="Reset password")
            button.set_valign(Gtk.Align.CENTER)
            button.connect(
                "clicked",
                lambda _button, username=str(user.get("name") or ""): self._password_dialog(username),
            )
            row.add_suffix(button)
            users.add(row)

        actions = Adw.PreferencesGroup(
            title="Recovery tools",
            description="Additional offline operations will appear here as they become available.",
        )
        self.content.append(actions)
        browse = Adw.ActionRow(
            title="Browse and copy files",
            subtitle="Open the offline system without changing it",
        )
        browse.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        browse.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        browse.set_activatable(True)
        browse.connect("activated", lambda *_: FileBrowserWindow(self, target).present())
        actions.add(browse)
        snapshots = Adw.ActionRow(
            title="Manage Btrfs snapshots",
            subtitle="Create recovery points or restore the offline system",
        )
        snapshots.add_prefix(Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))
        if system.get("btrfs_layout"):
            snapshots.set_activatable(True)
            snapshots.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            snapshots.connect(
                "activated", lambda *_: SnapshotWindow(self, target).present()
            )
        else:
            snapshots.set_subtitle("Requires the standard AnduinOS Btrfs layout")
            snapshots.set_sensitive(False)
        actions.add(snapshots)

        self.stack.set_visible_child_name("content")
        return GLib.SOURCE_REMOVE

    def _password_dialog(self, username: str) -> None:
        password = Adw.PasswordEntryRow(title="New password")
        confirmation = Adw.PasswordEntryRow(title="Confirm new password")
        form = Adw.PreferencesGroup()
        form.add(password)
        form.add(confirmation)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Reset the password for {username}?",
            body=(
                "This changes the local login password on the selected offline system. "
                "It does not unlock an existing encrypted login keyring."
            ),
            extra_child=form,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset password")
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
            self.status.set_title("Resetting the password")
            self.status.set_description("Writing the new local account password safely…")
            self.stack.set_visible_child_name("status")
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
            heading="Password reset complete",
            body=f"The local login password for {username} was changed.",
        )
        done.add_response("close", "Close")
        done.set_default_response("close")
        done.set_close_response("close")
        done.present()
        target = self._target_payload.get("target", {})
        self._open_system(target)
        return GLib.SOURCE_REMOVE


class FileBrowserWindow(Adw.Window):
    def __init__(self, parent: Gtk.Window, target: dict):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Recover files")
        self.set_default_size(760, 620)
        self.target = target
        self.current = "."
        self.parent_path: str | None = None

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Parent folder")
        self.up.set_sensitive(False)
        self.up.connect("clicked", self._go_up)
        header.pack_start(self.up)
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self.stack)
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.page.set_margin_top(12)
        self.page.set_margin_bottom(12)
        self.page.set_margin_start(16)
        self.page.set_margin_end(16)
        self.location = Gtk.Label(xalign=0)
        self.location.add_css_class("heading")
        self.page.append(self.location)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        scroll.set_child(self.listbox)
        self.page.append(scroll)
        self.stack.add_named(self.page, "files")
        self.load(".")

    def load(self, relative: str) -> None:
        self.status.set_icon_name("folder-open-symbolic")
        self.status.set_title("Opening offline files")
        self.status.set_description("Reading this folder without changing the installed system…")
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
            subtitle = kind.capitalize()
            if kind == "file":
                subtitle += " · " + _size(entry.get("size"))
            row = Adw.ActionRow(title=str(entry.get("name") or ""), subtitle=subtitle)
            icon = "folder-symbolic" if kind == "directory" else "text-x-generic-symbolic"
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            if kind in {"directory", "file"}:
                export = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Copy out")
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
                    title="This folder contains more items",
                    subtitle="Only the first 5,000 entries are shown.",
                )
            )
        self.stack.set_visible_child_name("files")
        return GLib.SOURCE_REMOVE

    def _go_up(self, _button: Gtk.Button) -> None:
        if isinstance(self.parent_path, str):
            self.load(self.parent_path)

    def _failed(self, message: str) -> bool:
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title("Could not read offline files")
        self.status.set_description(message)
        self.stack.set_visible_child_name("status")
        return GLib.SOURCE_REMOVE

    def _choose_export(self, entry: dict) -> None:
        chooser = Gtk.FileDialog(title="Choose where to copy this item", modal=True)

        def selected(dialog, result) -> None:
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            destination = folder.get_path()
            if destination:
                self._export(entry, destination)

        chooser.select_folder(self, None, selected)

    def _export(self, entry: dict, destination: str) -> None:
        self.status.set_icon_name("document-save-symbolic")
        self.status.set_title("Copying rescued data")
        self.status.set_description("Keep the computer powered on until copying finishes.")
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
            transient_for=self,
            heading="Copy complete",
            body=f"The rescued item was copied to:\n{path}",
        )
        dialog.add_response("close", "Close")
        dialog.present()
        self.load(self.current)
        return GLib.SOURCE_REMOVE


class SnapshotWindow(Adw.Window):
    def __init__(self, parent: Gtk.Window, target: dict):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("System snapshots")
        self.set_default_size(760, 620)
        self.target = target

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        create = Gtk.Button(label="Create snapshot")
        create.add_css_class("suggested-action")
        create.connect("clicked", self._create_dialog)
        header.pack_end(create)
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self.stack)
        self.status = Adw.StatusPage()
        self.stack.add_named(self.status, "status")
        scroll = Gtk.ScrolledWindow()
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_margin_top(18)
        self.listbox.set_margin_bottom(18)
        self.listbox.set_margin_start(24)
        self.listbox.set_margin_end(24)
        scroll.set_child(self.listbox)
        self.stack.add_named(scroll, "snapshots")
        self.load()

    def _target_identity(self) -> tuple[str, str]:
        return str(self.target.get("path") or ""), str(self.target.get("identity") or "")

    def load(self) -> None:
        self.status.set_icon_name("document-open-recent-symbolic")
        self.status.set_title("Loading system snapshots")
        self.status.set_description(
            "Checking recovery metadata and completing any interrupted restore…"
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
            self.status.set_title("No system snapshots")
            self.status.set_description("Create a snapshot before making risky system changes.")
            self.stack.set_visible_child_name("status")
            return GLib.SOURCE_REMOVE
        for deployment in deployments:
            if not isinstance(deployment, dict):
                continue
            title = str(deployment.get("title") or "System snapshot")
            state = str(deployment.get("state") or "unknown")
            kind = str(deployment.get("kind") or "snapshot")
            created = str(deployment.get("created_at") or "")
            row = Adw.ActionRow(
                title=title,
                subtitle=" · ".join(value for value in (created, kind, state) if value),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))
            if state == "ready" and isinstance(deployment.get("id"), str):
                restore = Gtk.Button(label="Restore")
                restore.set_valign(Gtk.Align.CENTER)
                restore.connect(
                    "clicked",
                    lambda _button, item=deployment: self._restore_dialog(item),
                )
                row.add_suffix(restore)
            else:
                badge = Gtk.Label(label="Unavailable")
                badge.add_css_class("dim-label")
                row.add_suffix(badge)
            self.listbox.append(row)
        self.stack.set_visible_child_name("snapshots")
        return GLib.SOURCE_REMOVE

    def _failed(self, message: str) -> bool:
        self.status.set_icon_name("dialog-error-symbolic")
        self.status.set_title("Snapshot operation failed")
        self.status.set_description(message)
        self.stack.set_visible_child_name("status")
        return GLib.SOURCE_REMOVE

    def _create_dialog(self, _button: Gtk.Button) -> None:
        name = Adw.EntryRow(title="Snapshot name")
        form = Adw.PreferencesGroup()
        form.add(name)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Create a snapshot of the offline system?",
            body="Personal files in Home are not included in system snapshots.",
            extra_child=form,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
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
        self.status.set_title("Creating system snapshot")
        self.status.set_description("Keep the computer powered on until this finishes.")
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
        protect = Gtk.CheckButton(label="Create a safety snapshot of the current system first")
        protect.set_active(True)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Restore {deployment.get('title') or 'this snapshot'}?",
            body=(
                "The offline system root will be replaced immediately. Personal files in Home "
                "will not be changed. Do not power off the computer during recovery."
            ),
            extra_child=protect,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore system")
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
        self.status.set_title("Restoring the offline system")
        self.status.set_description("Keep the computer powered on until recovery is complete.")
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
            transient_for=self,
            heading="System restore complete",
            body="Shut down the Live session, remove the installation media, and start AnduinOS.",
        )
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.connect("response", lambda *_: self.close())
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
