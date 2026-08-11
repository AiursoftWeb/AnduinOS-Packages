"""Structured fwupd integration for the Driver Center frontend."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import html
import re
import time
from typing import Callable

import gi

gi.require_version("Fwupd", "2.0")
from gi.repository import Fwupd, GLib  # noqa: E402


# These values are part of the stable fwupd D-Bus ABI.  fwupd 1.9 did not
# expose DeviceFlags through introspection, while fwupd 2.x does, so keeping
# the small subset used by the UI here preserves Noble compatibility.
DEVICE_FLAG_UPDATABLE = 1 << 1
DEVICE_FLAG_REQUIRE_AC = 1 << 3
DEVICE_FLAG_SUPPORTED = 1 << 5
DEVICE_FLAG_NEEDS_REBOOT = 1 << 8
DEVICE_FLAG_NEEDS_SHUTDOWN = 1 << 17
DEVICE_FLAG_AFFECTS_FDE = 1 << 45

REMOTE_KIND_DOWNLOAD = 1
REMOTE_FLAG_ENABLED = 1 << 0

# Tell fwupd that this frontend provides an update action, can surface device
# requests (replug, unlock, do-not-power-off), and can display disk-encryption
# warnings.  These feature bits are stable across fwupd 1.9 and 2.x.
CLIENT_FEATURE_FLAGS = (1 << 2) | (1 << 4) | (1 << 5)


@dataclass(frozen=True)
class FirmwareRelease:
    version: str
    name: str | None = None
    summary: str | None = None
    description: str | None = None
    urgency: int = 0
    remote_id: str | None = None


@dataclass(frozen=True)
class FirmwareDevice:
    device_id: str
    name: str
    vendor: str | None
    version: str | None
    summary: str | None
    updatable: bool
    supported: bool
    require_ac: bool
    needs_reboot: bool
    needs_shutdown: bool
    affects_fde: bool
    update_state: int
    update_error: str | None
    release: FirmwareRelease | None = None


@dataclass(frozen=True)
class FirmwareHistoryEntry:
    device_id: str
    name: str
    version: str | None
    state: int
    error: str | None
    timestamp: int


@dataclass(frozen=True)
class FirmwareSnapshot:
    connected: bool = False
    loading: bool = True
    busy: bool = False
    operation: str | None = None
    daemon_version: str | None = None
    devices: tuple[FirmwareDevice, ...] = field(default_factory=tuple)
    history: tuple[FirmwareHistoryEntry, ...] = field(default_factory=tuple)
    error: str | None = None
    last_refresh: int | None = None
    restart_required: bool = False
    shutdown_required: bool = False

    @property
    def updates(self) -> tuple[FirmwareDevice, ...]:
        return tuple(device for device in self.devices if device.release is not None)


def plain_text(value: str | None) -> str | None:
    """Reduce AppStream's small HTML subset to readable GTK label text."""
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text or None


def release_from_object(release) -> FirmwareRelease:
    return FirmwareRelease(
        version=release.get_version() or "",
        name=release.get_name(),
        summary=plain_text(release.get_summary()),
        description=plain_text(release.get_description()),
        urgency=int(release.get_urgency()),
        remote_id=release.get_remote_id(),
    )


def history_from_object(device) -> FirmwareHistoryEntry:
    return FirmwareHistoryEntry(
        device_id=device.get_id() or "",
        name=device.get_name() or "Firmware device",
        version=device.get_version(),
        state=int(device.get_update_state()),
        error=device.get_update_error(),
        timestamp=int(device.get_modified()),
    )


def device_from_object(device, release=None) -> FirmwareDevice:
    flags = int(device.get_flags())
    return FirmwareDevice(
        device_id=device.get_id() or "",
        name=device.get_name() or "Firmware device",
        vendor=device.get_vendor(),
        version=device.get_version(),
        summary=plain_text(device.get_summary()),
        updatable=bool(flags & DEVICE_FLAG_UPDATABLE),
        supported=bool(flags & DEVICE_FLAG_SUPPORTED),
        require_ac=bool(flags & DEVICE_FLAG_REQUIRE_AC),
        needs_reboot=bool(flags & DEVICE_FLAG_NEEDS_REBOOT),
        needs_shutdown=bool(flags & DEVICE_FLAG_NEEDS_SHUTDOWN),
        affects_fde=bool(flags & DEVICE_FLAG_AFFECTS_FDE),
        update_state=int(device.get_update_state()),
        update_error=device.get_update_error(),
        release=release_from_object(release) if release else None,
    )


StateCallback = Callable[[FirmwareSnapshot], None]
ProgressCallback = Callable[[int, int], None]
DoneCallback = Callable[[str, bool, str | None, bool, bool], None]
RequestCallback = Callable[[str], None]


class FirmwareManager:
    """Own a single asynchronous fwupd client and its UI-facing snapshot."""

    def __init__(
        self,
        state_changed: StateCallback,
        progress_changed: ProgressCallback,
        operation_done: DoneCallback,
        request_received: RequestCallback,
        client=None,
    ) -> None:
        self.snapshot = FirmwareSnapshot()
        self._state_changed = state_changed
        self._progress_changed = progress_changed
        self._operation_done = operation_done
        self._request_received = request_received
        self._client = client or Fwupd.Client.new()
        self._device_objects: dict[str, object] = {}
        self._release_objects: dict[str, object] = {}
        self._generation = 0
        self._reload_timer = 0
        self._pending_completion: tuple[str, bool, str | None, bool, bool] | None = None
        self._install_queue: list[str] = []
        self._operation_restart = False
        self._operation_shutdown = False

        self._client.connect("notify::percentage", self._progress_notify)
        self._client.connect("notify::status", self._progress_notify)
        self._client.connect("changed", self._daemon_changed)
        self._client.connect("device-changed", self._device_changed)
        self._client.connect("device-request", self._device_request)

    def start(self) -> None:
        self._client.connect_async(None, self._connected)

    def _emit_state(self) -> None:
        self._state_changed(self.snapshot)

    def _connected(self, client, result) -> None:
        try:
            client.connect_finish(result)
            client.set_user_agent_for_package("anduinos-driver-center", "2.0.0")
            client.set_feature_flags(Fwupd.FeatureFlags(CLIENT_FEATURE_FLAGS))
        except GLib.Error as error:
            self.snapshot = replace(
                self.snapshot,
                loading=False,
                error=str(error),
            )
            self._emit_state()
            return
        self.snapshot = replace(
            self.snapshot,
            connected=True,
            daemon_version=client.get_daemon_version(),
            error=None,
        )
        self._emit_state()
        self.reload()

    def reload(
        self, completion: str | None = None, *, force: bool = False
    ) -> None:
        if not self.snapshot.connected:
            if not self.snapshot.loading:
                self.snapshot = replace(self.snapshot, loading=True, error=None)
                self._emit_state()
                self._client.connect_async(None, self._connected)
            return
        if self.snapshot.busy and completion is None and not force:
            return
        self._generation += 1
        generation = self._generation
        if completion:
            self._pending_completion = (completion, True, None, False, False)
        self.snapshot = replace(
            self.snapshot,
            loading=True,
            busy=bool(completion) or self.snapshot.busy,
            operation=completion or self.snapshot.operation,
            error=None,
        )
        self._emit_state()

        context = {
            "devices_done": False,
            "history_done": False,
            "pending_upgrades": 0,
            "devices": [],
            "history": [],
            "objects": {},
            "releases": {},
        }

        def maybe_finish() -> None:
            if generation != self._generation:
                return
            if not context["devices_done"] or not context["history_done"]:
                return
            if context["pending_upgrades"]:
                return
            devices = tuple(sorted(context["devices"], key=lambda item: item.name.lower()))
            history = tuple(
                sorted(
                    context["history"],
                    key=lambda item: item.timestamp,
                    reverse=True,
                )
            )
            self._device_objects = context["objects"]
            self._release_objects = context["releases"]
            restart_pending = any(
                entry.state == int(Fwupd.UpdateState.NEEDS_REBOOT)
                for entry in history
            )
            self.snapshot = replace(
                self.snapshot,
                loading=False,
                busy=False,
                operation=None,
                devices=devices,
                history=history,
                error=None,
                restart_required=(
                    self.snapshot.restart_required or restart_pending
                ),
            )
            self._emit_state()
            self._complete_pending()

        def upgrade_done(client, result, raw_device) -> None:
            if generation != self._generation:
                return
            releases = []
            try:
                releases = client.get_upgrades_finish(result)
            except GLib.Error as error:
                if not self._is_empty_result(error):
                    # A single unsupported device must not hide all other
                    # firmware, but retain the detail on its device row.
                    raw_device.set_update_error(str(error))
            release = releases[0] if releases else None
            device_id = raw_device.get_id() or ""
            model = device_from_object(raw_device, release)
            context["devices"].append(model)
            context["objects"][device_id] = raw_device
            if release:
                context["releases"][device_id] = release
            context["pending_upgrades"] -= 1
            maybe_finish()

        def devices_done(client, result) -> None:
            if generation != self._generation:
                return
            try:
                raw_devices = client.get_devices_finish(result)
            except GLib.Error as error:
                self._load_failed(str(error))
                return
            managed = []
            for raw_device in raw_devices:
                flags = int(raw_device.get_flags())
                if flags & (DEVICE_FLAG_UPDATABLE | DEVICE_FLAG_SUPPORTED):
                    managed.append(raw_device)
            context["pending_upgrades"] = len(managed)
            context["devices_done"] = True
            for raw_device in managed:
                client.get_upgrades_async(
                    raw_device.get_id(),
                    None,
                    upgrade_done,
                    raw_device,
                )
            maybe_finish()

        def history_done(client, result) -> None:
            if generation != self._generation:
                return
            try:
                raw_history = client.get_history_finish(result)
            except GLib.Error as error:
                raw_history = [] if self._is_empty_result(error) else []
            context["history"] = [
                history_from_object(device) for device in raw_history
            ]
            context["history_done"] = True
            maybe_finish()

        self._client.get_devices_async(None, devices_done)
        self._client.get_history_async(None, history_done)

    def _load_failed(self, message: str) -> None:
        self.snapshot = replace(
            self.snapshot,
            loading=False,
            busy=False,
            operation=None,
            error=message,
        )
        self._emit_state()
        if self._pending_completion:
            action = self._pending_completion[0]
            self._pending_completion = None
            self._operation_done(action, False, message, False, False)

    @staticmethod
    def _is_empty_result(error: GLib.Error) -> bool:
        return any(
            error.matches(Fwupd.error_quark(), code)
            for code in (
                Fwupd.Error.NOTHING_TO_DO,
                Fwupd.Error.NOT_FOUND,
                Fwupd.Error.NOT_SUPPORTED,
            )
        )

    def refresh_metadata(self) -> None:
        if self.snapshot.busy or not self.snapshot.connected:
            return
        self.snapshot = replace(
            self.snapshot,
            busy=True,
            operation="refresh",
            error=None,
        )
        self._emit_state()

        def remotes_done(client, result) -> None:
            try:
                remotes = client.get_remotes_finish(result)
            except GLib.Error as error:
                self._operation_failed("refresh", str(error))
                return
            enabled = [
                remote
                for remote in remotes
                if int(remote.get_kind()) == REMOTE_KIND_DOWNLOAD
                and int(remote.get_flags()) & REMOTE_FLAG_ENABLED
            ]
            self._refresh_next(enabled)

        self._client.get_remotes_async(None, remotes_done)

    def _refresh_next(self, remotes: list[object]) -> None:
        if not remotes:
            self.snapshot = replace(self.snapshot, last_refresh=int(time.time()))
            self._pending_completion = ("refresh", True, None, False, False)
            self.reload(force=True)
            return
        remote = remotes.pop(0)

        def refreshed(client, result) -> None:
            try:
                client.refresh_remote_finish(result)
            except GLib.Error as error:
                self._operation_failed("refresh", str(error))
                return
            self._refresh_next(remotes)

        try:
            self._client.refresh_remote_async(
                remote,
                Fwupd.ClientDownloadFlags(0),
                None,
                refreshed,
            )
        except TypeError:
            # fwupd 1.9 did not have the download_flags parameter.
            self._client.refresh_remote_async(remote, None, refreshed)

    def check_updates(self) -> None:
        if self.snapshot.busy:
            return
        self.reload("check")

    def install(self, device_ids: list[str]) -> None:
        selected = [
            device_id
            for device_id in device_ids
            if device_id in self._device_objects
            and device_id in self._release_objects
        ]
        if not selected or self.snapshot.busy:
            return
        self._install_queue = selected
        self._operation_restart = False
        self._operation_shutdown = False
        self.snapshot = replace(
            self.snapshot,
            busy=True,
            operation="update",
            error=None,
        )
        self._emit_state()
        self._install_next()

    def _install_next(self) -> None:
        if not self._install_queue:
            self._pending_completion = (
                "update",
                True,
                None,
                self._operation_restart,
                self._operation_shutdown,
            )
            self.snapshot = replace(
                self.snapshot,
                restart_required=(
                    self.snapshot.restart_required or self._operation_restart
                ),
                shutdown_required=(
                    self.snapshot.shutdown_required or self._operation_shutdown
                ),
            )
            self.reload(force=True)
            return
        device_id = self._install_queue.pop(0)
        device = self._device_objects[device_id]
        release = self._release_objects[device_id]
        flags = int(device.get_flags())
        self._operation_restart |= bool(flags & DEVICE_FLAG_NEEDS_REBOOT)
        self._operation_shutdown |= bool(flags & DEVICE_FLAG_NEEDS_SHUTDOWN)

        def installed(client, result) -> None:
            try:
                client.install_release_finish(result)
            except GLib.Error as error:
                self._operation_failed("update", str(error))
                return
            self._install_next()

        try:
            self._client.install_release_async(
                device,
                release,
                Fwupd.InstallFlags(0),
                Fwupd.ClientDownloadFlags(0),
                None,
                installed,
            )
        except TypeError:
            # fwupd 1.9 did not have the download_flags parameter.
            self._client.install_release_async(
                device,
                release,
                Fwupd.InstallFlags(0),
                None,
                installed,
            )

    def _operation_failed(self, action: str, message: str) -> None:
        self._install_queue = []
        restart_required = action == "update" and self._operation_restart
        shutdown_required = action == "update" and self._operation_shutdown
        self.snapshot = replace(
            self.snapshot,
            busy=False,
            loading=False,
            operation=None,
            error=message,
            restart_required=(
                self.snapshot.restart_required or restart_required
            ),
            shutdown_required=(
                self.snapshot.shutdown_required or shutdown_required
            ),
        )
        self._emit_state()
        self._operation_done(
            action,
            False,
            message,
            restart_required,
            shutdown_required,
        )

    def _complete_pending(self) -> None:
        if not self._pending_completion:
            return
        completion = self._pending_completion
        self._pending_completion = None
        self._operation_done(*completion)

    def _progress_notify(self, client, _parameter) -> None:
        self._progress_changed(
            int(client.get_status()),
            int(client.get_percentage()),
        )

    def _device_request(self, _client, request) -> None:
        message = request.get_message() or request.get_id()
        if message:
            self._request_received(plain_text(message) or message)

    def _device_changed(self, _client, device) -> None:
        if self.snapshot.operation != "update":
            return
        flags = int(device.get_flags())
        self._operation_restart |= bool(flags & DEVICE_FLAG_NEEDS_REBOOT)
        self._operation_shutdown |= bool(flags & DEVICE_FLAG_NEEDS_SHUTDOWN)

    def _daemon_changed(self, *_args) -> None:
        if self.snapshot.busy or self.snapshot.loading:
            return
        if self._reload_timer:
            GLib.source_remove(self._reload_timer)
        self._reload_timer = GLib.timeout_add(500, self._reload_after_change)

    def _reload_after_change(self) -> bool:
        self._reload_timer = 0
        self.reload()
        return GLib.SOURCE_REMOVE
