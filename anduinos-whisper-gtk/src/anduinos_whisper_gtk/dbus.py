"""Client helpers for the voice-typing session service."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gio, GLib

from anduinos_whisper_framework import APP_ID, INTERFACE, OBJECT_PATH


class VoiceServiceClient:
    def __init__(self) -> None:
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.proxy = Gio.DBusProxy.new_sync(
            self.connection,
            Gio.DBusProxyFlags.NONE,
            None,
            APP_ID,
            OBJECT_PATH,
            INTERFACE,
            None,
        )
        self._subscriptions: list[int] = []

    def call(self, method: str) -> None:
        self.proxy.call(
            method,
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._call_finished,
            None,
        )

    def call_sync(self, method: str) -> GLib.Variant | None:
        return self.proxy.call_sync(method, None, Gio.DBusCallFlags.NONE, -1, None)

    def state(self) -> tuple[str, str]:
        result = self.call_sync("GetState")
        return result.unpack() if result is not None else ("idle", "Ready")

    def subscribe(self, signal: str, callback: Callable[..., None]) -> None:
        identifier = self.connection.signal_subscribe(
            APP_ID,
            INTERFACE,
            signal,
            OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            lambda _connection, _sender, _path, _interface, _signal, parameters: callback(
                *parameters.unpack()
            ),
        )
        self._subscriptions.append(identifier)

    def close(self) -> None:
        for identifier in self._subscriptions:
            self.connection.signal_unsubscribe(identifier)
        self._subscriptions.clear()

    @staticmethod
    def _call_finished(proxy: Gio.DBusProxy, result: Gio.AsyncResult, _data) -> None:
        try:
            proxy.call_finish(result)
        except GLib.Error:
            pass

