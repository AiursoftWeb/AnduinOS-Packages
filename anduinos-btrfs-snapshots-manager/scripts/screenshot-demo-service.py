#!/usr/bin/python3
"""Read-only D-Bus fixture used only to capture branded AppStream images."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


SERVICE = "org.anduinos.BtrfsSnapshotsManager"
OBJECT = "/org/anduinos/BtrfsSnapshotsManager"
INTERFACE = "org.anduinos.BtrfsSnapshotsManager.Helper"


def _timestamp(days: int = 0, hours: int = 0) -> str:
    value = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours)
    return value.isoformat().replace("+00:00", "Z")


DEPLOYMENTS = [
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e01",
        "kind": "automatic",
        "state": "ready",
        "created_at": _timestamp(hours=2),
        "title": "daily-20260805-0800",
        "reason": "Daily automatic system snapshot",
        "schedule_id": "daily",
        "kernel_release": "6.17.0-4-generic",
        "pinned": False,
    },
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e02",
        "kind": "manual",
        "state": "ready",
        "created_at": _timestamp(days=1),
        "title": "Before graphics driver update",
        "reason": "Known-good system before installing the new driver",
        "schedule_id": None,
        "kernel_release": "6.17.0-4-generic",
        "pinned": True,
    },
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e03",
        "kind": "apt-post",
        "state": "ready",
        "created_at": _timestamp(days=3),
        "title": "After package changes",
        "reason": "Automatic system snapshot for a package transaction",
        "schedule_id": None,
        "kernel_release": "6.17.0-3-generic",
        "pinned": False,
    },
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e04",
        "kind": "automatic",
        "state": "ready",
        "created_at": _timestamp(days=7),
        "title": "weekly-20260729-0900",
        "reason": "Weekly automatic system snapshot",
        "schedule_id": "weekly",
        "kernel_release": "6.17.0-3-generic",
        "pinned": False,
    },
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e05",
        "kind": "automatic",
        "state": "ready",
        "created_at": _timestamp(days=10),
        "title": "daily-20260726-0800",
        "reason": "Daily automatic system snapshot",
        "schedule_id": "daily",
        "kernel_release": "6.17.0-3-generic",
        "pinned": False,
    },
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e06",
        "kind": "apt-pre",
        "state": "ready",
        "created_at": _timestamp(days=14),
        "title": "Before package changes",
        "reason": "Automatic system snapshot before a package transaction",
        "schedule_id": None,
        "kernel_release": "6.17.0-2-generic",
        "pinned": False,
    },
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e07",
        "kind": "manual",
        "state": "ready",
        "created_at": _timestamp(days=18),
        "title": "Stable workstation setup",
        "reason": "All development tools verified",
        "schedule_id": None,
        "kernel_release": "6.17.0-2-generic",
        "pinned": False,
    },
    {
        "id": "53c28947-d11e-4f42-99e5-1f6621f00e08",
        "kind": "automatic",
        "state": "ready",
        "created_at": _timestamp(days=21),
        "title": "weekly-20260715-0900",
        "reason": "Weekly automatic system snapshot",
        "schedule_id": "weekly",
        "kernel_release": "6.17.0-1-generic",
        "pinned": False,
    },
]


class ScreenshotFixture(dbus.service.Object):
    def __init__(self, bus: dbus.Bus) -> None:
        super().__init__(bus, OBJECT)

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s")
    def GetRecoveryEngineStatus(self) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "available": True,
                "pending": None,
                "deployment_count": len(DEPLOYMENTS),
                "deployments": DEPLOYMENTS,
                "personal_snapshot_count": 0,
                "personal_snapshots": [],
                "personal_issues": [],
                "system_package_counts": {
                    deployment["id"]: 2314 + index * 7
                    for index, deployment in enumerate(DEPLOYMENTS)
                },
                "personal_sizes": {},
                "issues": [],
                "layout": {
                    "support": "supported",
                    "root_filesystem": "btrfs",
                    "issues": [],
                },
            }
        )

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s")
    def GetAptHistory(self) -> str:
        return json.dumps({"transactions": [], "issues": []})

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s")
    def GetSchedulerStatus(self) -> str:
        return "running"

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s")
    def GetAutomationConfig(self) -> str:
        policy = {
            "is_auto_snapshot_enabled": True,
            "snapshot_interval_hours": 1,
            "is_auto_cleanup_enabled": True,
            "keep_all_hours": 24,
            "keep_daily_days": 7,
            "keep_weekly_days": 30,
            "keep_monthly_days": 365,
            "keep_yearly": True,
        }
        return json.dumps(
            {
                "schema_version": 1,
                "system": policy,
                "home": policy,
                "notifications": {
                    "notify_before_scheduled": False,
                    "notify_after_success": True,
                    "notify_after_cleanup": False,
                },
            }
        )

    @dbus.service.method(INTERFACE, in_signature="", out_signature="bb")
    def GetAptSnapshotPolicy(self) -> tuple[bool, bool]:
        return True, False

    @dbus.service.signal(INTERFACE, signature="ss")
    def SnapshotCreated(self, _snapshot_name: str, _created_by: str) -> None:
        return None

    @dbus.service.signal(INTERFACE, signature="ss")
    def PersonalSnapshotCreated(self, _snapshot_name: str, _created_by: str) -> None:
        return None

    @dbus.service.signal(INTERFACE, signature="sb")
    def SnapshotCreationSucceeded(self, _scope: str, _automatic: bool) -> None:
        return None

def main() -> int:
    if os.environ.get("ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_SCREENSHOT_DEMO") != "1":
        print("This fixture is only for controlled screenshot capture.", file=sys.stderr)
        return 2
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    name = dbus.service.BusName(SERVICE, bus=bus)
    fixture = ScreenshotFixture(bus)
    GLib.MainLoop().run()
    del fixture
    del name
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
