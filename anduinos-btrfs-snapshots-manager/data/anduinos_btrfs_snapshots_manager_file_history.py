"""Nautilus 4 menu provider for Disk Snapshots Manager Personal Files history.

This extension deliberately contains no snapshot or privileged operations. It
only validates a local selection and activates the separately installed
Disk Snapshots Manager application over the user's session bus.
"""

from __future__ import annotations

import gettext
import logging
import os
import stat

import gi

try:
    # Nautilus 50 publishes the API-4 namespace as 4.1. Older supported
    # Nautilus releases publish the same extension API as 4.0.
    gi.require_version("Nautilus", "4.1")
except ValueError:
    gi.require_version("Nautilus", "4.0")
from gi.repository import Gio, GLib, GObject, Nautilus  # noqa: E402


DOMAIN = "anduinos-btrfs-snapshots-manager"
BUS_NAME = "org.anduinos.BtrfsSnapshotsManager"
OBJECT_PATH = "/org/anduinos/BtrfsSnapshotsManager"
ACTION_NAME = "file-history"
gettext.bindtextdomain(DOMAIN, "/usr/share/locale")


def _(message: str) -> str:
    return gettext.dgettext(DOMAIN, message)


def _local_home_uri(file_info: Nautilus.FileInfo, require_directory: bool) -> str | None:
    location = file_info.get_location()
    if location is None or not location.is_native():
        return None
    if location.get_uri_scheme() != "file":
        return None
    path = location.get_path()
    if path is None:
        return None

    home = os.path.realpath(os.path.expanduser("~"))
    if os.path.dirname(home) != "/home":
        return None
    normalized = os.path.normpath(os.path.abspath(path))
    try:
        resolved = os.path.realpath(normalized, strict=True)
        metadata = os.lstat(normalized)
        inside_home = os.path.commonpath((home, resolved)) == home
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError):
        return None

    # Refuse symlinks, including symlinked parent components. Disk Snapshots Manager performs
    # the same validation again before turning this into a relative @home path.
    if resolved != normalized or stat.S_ISLNK(metadata.st_mode) or not inside_home:
        return None
    is_directory = stat.S_ISDIR(metadata.st_mode)
    is_regular = stat.S_ISREG(metadata.st_mode)
    if require_directory and not is_directory:
        return None
    if not (is_directory or is_regular):
        return None
    return location.get_uri()


def _activate_snapshots_manager(mode: str, uri: str) -> None:
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        actions = Gio.DBusActionGroup.get(connection, BUS_NAME, OBJECT_PATH)
        actions.activate_action(ACTION_NAME, GLib.Variant("(ss)", (mode, uri)))
    except GLib.Error as error:
        logging.getLogger(__name__).warning("Could not activate Disk Snapshots Manager: %s", error)


class SnapshotsManagerFileHistoryProvider(GObject.GObject, Nautilus.MenuProvider):
    """Expose focused history actions only for safe local home paths."""

    def get_file_items(self, files):
        if len(files) != 1:
            return ()
        uri = _local_home_uri(files[0], require_directory=False)
        if uri is None:
            return ()
        item = Nautilus.MenuItem(
            name="AnduinOSBtrfsSnapshotsManager::ViewFileHistory",
            label=_("View File History…"),
            tip=_("Browse earlier Personal Files versions of this item"),
        )
        item.connect("activate", lambda _item: _activate_snapshots_manager("selection", uri))
        return (item,)

    def get_background_items(self, current_folder):
        uri = _local_home_uri(current_folder, require_directory=True)
        if uri is None:
            return ()
        item = Nautilus.MenuItem(
            name="AnduinOSBtrfsSnapshotsManager::BrowseFolderHistory",
            label=_("Browse This Folder’s History…"),
            tip=_("Find files that existed earlier in this Personal Files folder"),
        )
        item.connect("activate", lambda _item: _activate_snapshots_manager("folder", uri))
        return (item,)
