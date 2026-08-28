"""GNOME Shell SearchProvider2 service for Control Panel topics."""

from __future__ import annotations

import shutil
import warnings

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .topics import ControlPanelTopic, get_topic, search_topics


BUS_NAME = "com.anduinos.ControlPanel.SearchProvider"
OBJECT_PATH = "/com/anduinos/ControlPanel/SearchProvider"
CONTROL_PANEL_EXECUTABLE = "/usr/bin/anduinos-control-panel"
IDLE_TIMEOUT_SECONDS = 30

SEARCH_PROVIDER_XML = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetResultMetas">
      <arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{sv}" name="metas" direction="out"/>
    </method>
    <method name="ActivateResult">
      <arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="LaunchSearch">
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
  </interface>
</node>
"""


def _topic_is_searchable(topic: ControlPanelTopic) -> bool:
    if not topic.availability_command:
        return True
    return shutil.which(topic.availability_command) is not None


def _result_ids(
    terms: tuple[str, ...] | list[str],
    candidates: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    return [
        topic.identifier
        for topic in search_topics(terms, candidates)
        if _topic_is_searchable(topic)
    ]


def _result_metas(
    identifiers: tuple[str, ...] | list[str],
) -> list[dict[str, GLib.Variant]]:
    metas: list[dict[str, GLib.Variant]] = []
    for identifier in identifiers:
        topic = get_topic(identifier)
        if topic is None or not _topic_is_searchable(topic):
            continue
        metas.append(
            {
                "id": GLib.Variant("s", topic.identifier),
                "name": GLib.Variant("s", topic.title),
                "description": GLib.Variant("s", topic.description),
                "gicon": GLib.Variant("s", topic.icon),
            }
        )
    return metas


def _spawn(arguments: list[str], timestamp: int) -> None:
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
    if timestamp:
        launcher.setenv("DESKTOP_STARTUP_ID", f"_TIME{timestamp}", True)
    launcher.spawnv(arguments)


def _activation_arguments(identifier: str) -> list[str] | None:
    topic = get_topic(identifier)
    if topic is None:
        return None
    if topic.command and (
        not topic.install_package or shutil.which(topic.command[0]) is not None
    ):
        return list(topic.command)
    return [CONTROL_PANEL_EXECUTABLE, "--topic", identifier]


class ControlPanelSearchProvider:
    def __init__(self, quit_callback):
        self._quit_callback = quit_callback
        self._registration_id = 0
        self._idle_source = 0
        node = Gio.DBusNodeInfo.new_for_xml(SEARCH_PROVIDER_XML)
        self._interface = node.interfaces[0]
        self._touch()

    def _touch(self) -> None:
        if self._idle_source:
            GLib.source_remove(self._idle_source)
        self._idle_source = GLib.timeout_add_seconds(
            IDLE_TIMEOUT_SECONDS, self._idle_timeout
        )

    def _idle_timeout(self) -> bool:
        self._idle_source = 0
        self._quit_callback()
        return GLib.SOURCE_REMOVE

    def export(self, connection: Gio.DBusConnection) -> None:
        # PyGObject has not exposed register_object_with_closures yet, although
        # recent Gio marks this closure-based binding as deprecated.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Gio.DBusConnection.register_object is deprecated",
                category=DeprecationWarning,
            )
            self._registration_id = connection.register_object(
                OBJECT_PATH,
                self._interface,
                self._handle_method_call,
                None,
                None,
            )

    def _handle_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        self._touch()
        try:
            if method_name == "GetInitialResultSet":
                (terms,) = parameters.unpack()
                invocation.return_value(GLib.Variant("(as)", (_result_ids(terms),)))
                return

            if method_name == "GetSubsearchResultSet":
                previous_results, terms = parameters.unpack()
                invocation.return_value(
                    GLib.Variant(
                        "(as)", (_result_ids(terms, previous_results),)
                    )
                )
                return

            if method_name == "GetResultMetas":
                (identifiers,) = parameters.unpack()
                invocation.return_value(
                    GLib.Variant("(aa{sv})", (_result_metas(identifiers),))
                )
                return

            if method_name == "ActivateResult":
                identifier, _terms, timestamp = parameters.unpack()
                arguments = _activation_arguments(identifier)
                if arguments is not None:
                    _spawn(arguments, timestamp)
                invocation.return_value(None)
                return

            if method_name == "LaunchSearch":
                terms, timestamp = parameters.unpack()
                _spawn(
                    [CONTROL_PANEL_EXECUTABLE, "--search", " ".join(terms)],
                    timestamp,
                )
                invocation.return_value(None)
                return

            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"Unknown search provider method: {method_name}",
            )
        except (GLib.Error, OSError, ValueError) as error:
            invocation.return_dbus_error(
                "com.anduinos.ControlPanel.SearchProvider.Error", str(error)
            )


def main() -> int:
    loop = GLib.MainLoop()
    provider = ControlPanelSearchProvider(loop.quit)

    def bus_acquired(connection: Gio.DBusConnection, _name: str) -> None:
        provider.export(connection)

    def name_lost(_connection: Gio.DBusConnection | None, _name: str) -> None:
        loop.quit()

    owner_id = Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        bus_acquired,
        None,
        name_lost,
    )
    try:
        loop.run()
    finally:
        Gio.bus_unown_name(owner_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
