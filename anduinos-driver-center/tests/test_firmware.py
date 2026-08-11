from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_driver_center.firmware import (  # noqa: E402
    DEVICE_FLAG_AFFECTS_FDE,
    DEVICE_FLAG_NEEDS_REBOOT,
    DEVICE_FLAG_NEEDS_SHUTDOWN,
    DEVICE_FLAG_REQUIRE_AC,
    DEVICE_FLAG_SUPPORTED,
    DEVICE_FLAG_UPDATABLE,
    FirmwareSnapshot,
    FirmwareManager,
    device_from_object,
    history_from_object,
    plain_text,
)
from gi.repository import Fwupd, GLib  # noqa: E402


class FakeRelease:
    def get_version(self): return "2.0.0"
    def get_name(self): return "Test Firmware"
    def get_summary(self): return "<p>Security &amp; reliability fixes</p>"
    def get_description(self): return "<p>Detailed release notes</p>"
    def get_urgency(self): return 4
    def get_remote_id(self): return "lvfs"


class FakeDevice:
    def get_id(self): return "device-id"
    def get_name(self): return "System Firmware"
    def get_vendor(self): return "AnduinOS Hardware"
    def get_version(self): return "1.0.0"
    def get_summary(self): return "<p>UEFI firmware</p>"
    def get_flags(self):
        return (
            DEVICE_FLAG_UPDATABLE
            | DEVICE_FLAG_SUPPORTED
            | DEVICE_FLAG_REQUIRE_AC
            | DEVICE_FLAG_NEEDS_REBOOT
            | DEVICE_FLAG_AFFECTS_FDE
        )
    def get_update_state(self): return 2
    def get_update_error(self): return None
    def get_modified(self): return 1_700_000_000


class FakeRemote:
    def get_kind(self): return 1
    def get_flags(self): return 1


class FakeClient:
    def __init__(self, legacy_install=False, legacy_refresh=False):
        self.legacy_install = legacy_install
        self.legacy_refresh = legacy_refresh
        self.install_calls = []
        self.refresh_calls = []

    def connect(self, *_args): return 1
    def get_status(self): return 1
    def get_percentage(self): return 0
    def get_remotes_async(self, _cancel, callback): callback(self, object())
    def get_remotes_finish(self, _result): return [FakeRemote()]

    def refresh_remote_async(self, *args):
        if self.legacy_refresh and len(args) == 4:
            raise TypeError("legacy fwupd signature")
        self.refresh_calls.append(args)
        callback = args[-1]
        callback(self, object())

    def refresh_remote_finish(self, _result): return True

    def install_release_async(self, *args):
        if self.legacy_install and len(args) == 6:
            raise TypeError("legacy fwupd signature")
        self.install_calls.append(args)
        callback = args[-1]
        callback(self, object())

    def install_release_finish(self, _result): return True


class PartialFailureClient(FakeClient):
    def install_release_finish(self, _result):
        if len(self.install_calls) == 2:
            raise GLib.Error.new_literal(
                Fwupd.error_quark(),
                "second device failed",
                int(Fwupd.Error.INTERNAL),
            )
        return True


class FirmwareTests(unittest.TestCase):
    def test_appstream_markup_is_reduced_to_plain_text(self):
        self.assertEqual(
            plain_text("<p>Security &amp; <b>reliability</b> fixes</p>"),
            "Security & reliability fixes",
        )

    def test_device_and_release_capabilities_are_preserved(self):
        device = device_from_object(FakeDevice(), FakeRelease())
        self.assertEqual(device.device_id, "device-id")
        self.assertEqual(device.version, "1.0.0")
        self.assertTrue(device.updatable)
        self.assertTrue(device.supported)
        self.assertTrue(device.require_ac)
        self.assertTrue(device.needs_reboot)
        self.assertFalse(device.needs_shutdown)
        self.assertTrue(device.affects_fde)
        self.assertEqual(device.release.version, "2.0.0")
        self.assertEqual(device.release.summary, "Security & reliability fixes")
        self.assertEqual(device.release.urgency, 4)

    def test_snapshot_exposes_only_devices_with_updates(self):
        available = device_from_object(FakeDevice(), FakeRelease())
        current = device_from_object(FakeDevice())
        snapshot = FirmwareSnapshot(devices=(available, current))
        self.assertEqual(snapshot.updates, (available,))

    def test_history_preserves_result_and_timestamp(self):
        entry = history_from_object(FakeDevice())
        self.assertEqual(entry.name, "System Firmware")
        self.assertEqual(entry.version, "1.0.0")
        self.assertEqual(entry.state, 2)
        self.assertEqual(entry.timestamp, 1_700_000_000)

    def test_install_all_runs_each_release_and_accumulates_restart_state(self):
        client = FakeClient()
        states = []
        manager = FirmwareManager(
            states.append,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            client=client,
        )
        first = FakeDevice()
        second = FakeDevice()
        manager.snapshot = FirmwareSnapshot(connected=True, loading=False)
        manager._device_objects = {"first": first, "second": second}
        manager._release_objects = {
            "first": FakeRelease(),
            "second": FakeRelease(),
        }
        manager.reload = lambda *_args, **_kwargs: None
        manager.install(["first", "second"])
        self.assertEqual(len(client.install_calls), 2)
        self.assertTrue(manager.snapshot.restart_required)
        self.assertEqual(manager._pending_completion[0], "update")

    def test_device_change_captures_post_install_shutdown_requirement(self):
        manager = FirmwareManager(
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            client=FakeClient(),
        )
        manager.snapshot = FirmwareSnapshot(
            connected=True,
            loading=False,
            busy=True,
            operation="update",
        )

        class ChangedDevice(FakeDevice):
            def get_flags(self):
                return DEVICE_FLAG_NEEDS_SHUTDOWN

        manager._device_changed(None, ChangedDevice())
        self.assertTrue(manager._operation_shutdown)

    def test_partial_batch_failure_preserves_restart_requirement(self):
        client = PartialFailureClient()
        completions = []
        manager = FirmwareManager(
            lambda *_args: None,
            lambda *_args: None,
            lambda *args: completions.append(args),
            lambda *_args: None,
            client=client,
        )
        manager.snapshot = FirmwareSnapshot(connected=True, loading=False)
        manager._device_objects = {
            "first": FakeDevice(),
            "second": FakeDevice(),
        }
        manager._release_objects = {
            "first": FakeRelease(),
            "second": FakeRelease(),
        }
        manager.install(["first", "second"])
        self.assertTrue(manager.snapshot.restart_required)
        self.assertEqual(completions[-1][0:2], ("update", False))
        self.assertTrue(completions[-1][3])

    def test_install_uses_fwupd_19_signature_when_required(self):
        client = FakeClient(legacy_install=True)
        manager = FirmwareManager(
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            client=client,
        )
        manager.snapshot = FirmwareSnapshot(connected=True, loading=False)
        manager._device_objects = {"device-id": FakeDevice()}
        manager._release_objects = {"device-id": FakeRelease()}
        manager.reload = lambda *_args, **_kwargs: None
        manager.install(["device-id"])
        self.assertEqual(len(client.install_calls), 1)
        self.assertEqual(len(client.install_calls[0]), 5)

    def test_refresh_updates_every_enabled_download_remote(self):
        client = FakeClient()
        manager = FirmwareManager(
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            client=client,
        )
        manager.snapshot = FirmwareSnapshot(connected=True, loading=False)
        manager.reload = lambda *_args, **_kwargs: None
        manager.refresh_metadata()
        self.assertEqual(len(client.refresh_calls), 1)
        self.assertIsNotNone(manager.snapshot.last_refresh)
        self.assertEqual(manager._pending_completion[0], "refresh")

    def test_refresh_uses_fwupd_19_signature_when_required(self):
        client = FakeClient(legacy_refresh=True)
        manager = FirmwareManager(
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            client=client,
        )
        manager.snapshot = FirmwareSnapshot(connected=True, loading=False)
        manager.reload = lambda *_args, **_kwargs: None
        manager.refresh_metadata()
        self.assertEqual(len(client.refresh_calls), 1)
        self.assertEqual(len(client.refresh_calls[0]), 3)


if __name__ == "__main__":
    unittest.main()
