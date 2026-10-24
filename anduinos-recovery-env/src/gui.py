#!/usr/bin/python3
"""Small offline browser; all system changes go through the recovery engine."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from uuid import UUID

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango


TOP = Path("/run/anduinos-rescue-center/repair-recovery-env")
STORE = "anduinos-btrfs-snapshots-manager"


def run(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{args[0]} failed")
    return result.stdout


class Recovery:
    def __init__(self):
        self.device = None
        self.mounted = False
        self.uuid = None

    def unmount(self):
        if self.mounted:
            run("umount", str(TOP))
            self.mounted = False

    def inspect(self):
        self.unmount()
        values = [v.split("=", 1)[1] for v in Path("/proc/cmdline").read_text().split()
                  if v.startswith("anduinos.recovery.root=")]
        if len(values) != 1:
            raise RuntimeError("The installed system was not specified by the boot menu.")
        self.uuid = str(UUID(values[0]))
        for _ in range(30):
            devices = subprocess.run(["blkid", "-c", "/dev/null", "-t", f"UUID={self.uuid}",
                                      "-o", "device"], text=True, capture_output=True).stdout.splitlines()
            if devices:
                break
            time.sleep(1)
        if len(devices) != 1:
            raise RuntimeError("The installed system is missing or its filesystem UUID is ambiguous.")
        self.device = devices[0]
        TOP.mkdir(parents=True, exist_ok=True)
        run("mount", "-t", "btrfs", "-o", "ro,rescue=nologreplay,subvolid=5", self.device, str(TOP))
        self.mounted = True
        report = json.loads(run("recovery-offline", "list", str(TOP)))
        if report.get("schema") != 1:
            raise RuntimeError("The snapshot catalog is not supported by this recovery environment.")
        return report

    def restore(self, identifier):
        # Reopen: a rescue=nologreplay mount cannot be remounted read-write.
        self.unmount()
        if run("blkid", "-p", "-s", "UUID", "-o", "value", self.device).strip() != self.uuid:
            raise RuntimeError("The selected system device has changed. Restart recovery.")
        run("mount", "-t", "btrfs", "-o", "rw,subvolid=5", self.device, str(TOP))
        self.mounted = True
        try:
            args = ("restore", str(TOP), str(UUID(identifier))) if identifier else ("resume", str(TOP))
            run("recovery-offline", *args)
            os.sync()
        finally:
            self.unmount()

    def restart(self):
        self.unmount()
        os.sync()
        run("reboot", "-f")

    def last_boot(self, records):
        path = TOP / "@snapshots" / STORE / "last-successful-boot.json"
        try:
            if path.is_symlink() or path.stat().st_size > 4096:
                return None
            value = json.loads(path.read_text())
            if value["schema"] != 1 or value["root_uuid"] != self.uuid:
                return None
            record = next((r for r in records if r["id"] == value["snapshot_id"]
                           and r.get("snapshot_uuid") == value["snapshot_uuid"] and restorable(r)), None)
            if record is None:
                return None
            snapshot = TOP / "@snapshots" / STORE / "deployments" / record["id"] / "root"
            actual = re.search(r"(?m)^\s*UUID:\s*(\S+)", run("btrfs", "subvolume", "show", str(snapshot)))
            if (snapshot.resolve() == snapshot and actual and actual[1] == record["snapshot_uuid"]
                    and run("btrfs", "property", "get", "-ts", str(snapshot), "ro").strip() == "ro=true"):
                return record["id"]
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            return None


def restorable(record):
    return record.get("state") == "ready" and not record.get("failure")


def snapshot_date(record):
    return datetime.fromisoformat(record["created_at"]).astimezone(timezone.utc).strftime("%b %d, %Y · %H:%M UTC")


def label(text, style=None):
    widget = Gtk.Label(label=text, xalign=0, wrap=True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if style:
        widget.add_css_class(style)
    return widget


def button(text, callback, primary=False):
    widget = Gtk.Button(label=text, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
    if primary:
        widget.add_css_class("suggested-action")
    widget.connect("clicked", lambda _: callback())
    return widget


class Browser(Gtk.Window):
    def __init__(self):
        super().__init__(title="AnduinOS Recovery")
        self.backend = Recovery()
        self.set_decorated(False)
        self.connect("close-request", lambda _: True)
        self.set_default_size(1024, 768)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20,
                            halign=Gtk.Align.CENTER, hexpand=True)
        self.body.set_size_request(680, -1)
        for edge in ("top", "bottom", "start", "end"):
            getattr(self.body, f"set_margin_{edge}")(32)
        self.set_child(self.body)
        self.present()
        self.load()

    def clear(self, title, subtitle):
        while child := self.body.get_first_child():
            self.body.remove(child)
        self.body.append(label(title, "title-1"))
        self.body.append(label(subtitle, "dim-label"))

    def work(self, title, subtitle, operation, done):
        self.clear(title, subtitle)
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, vexpand=True)
        spinner.set_size_request(40, 40)
        self.body.append(spinner)
        def worker():
            try:
                result = operation()
            except Exception as error:
                GLib.idle_add(self.failed, str(error))
            else:
                GLib.idle_add(done, result)
        threading.Thread(target=worker, daemon=True).start()

    def load(self):
        self.work("AnduinOS Recovery", "Looking for system snapshots…", self.backend.inspect, self.listed)

    def listed(self, report):
        if report["pending"]:
            self.clear("Finish the interrupted recovery", "A previously confirmed rollback did not finish. Resume it before choosing another snapshot.")
            self.body.append(button("Resume Recovery", lambda: self.recover(None), True))
            self.body.append(button("Restart", self.restart))
            return False
        self.clear("AnduinOS Recovery", "Choose a system snapshot to roll back to. Your personal files will stay unchanged.")
        records = report["deployments"]
        marked = self.backend.last_boot(records)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        rows.add_css_class("boxed-list")
        for record in records:
            row = Gtk.Box(spacing=24)
            for edge in ("top", "bottom", "start", "end"):
                getattr(row, f"set_margin_{edge}")(18)
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
            info.append(label(snapshot_date(record), "heading"))
            info.append(label(record["title"]))
            if not restorable(record):
                info.append(label("This snapshot is not ready for recovery.", "dim-label"))
            row.append(info)
            if record["id"] == marked:
                badge = label("Last successful boot", "last-boot")
                badge.set_wrap(False)
                badge.set_valign(Gtk.Align.CENTER)
                badge.set_tooltip_text("The recovery point recorded when the system last reached the graphical login screen or desktop. It is not a separate boot test of this snapshot.")
                row.append(badge)
            restore = button("Roll Back", lambda r=record: self.confirm(r), True)
            restore.set_sensitive(restorable(record))
            row.append(restore)
            rows.append(row)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(rows)
        self.body.append(scroll)
        if not records:
            self.body.append(label("No system snapshots are available."))
        if report.get("issues"):
            self.body.append(label("Some snapshots could not be read. Only recognized snapshots are listed.", "dim-label"))
        self.body.append(button("Restart", self.restart))
        return False

    def confirm(self, record):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.WARNING,
                                   text="Roll back the system?",
                                   secondary_text=f'{record["title"]}\n{snapshot_date(record)}\n\nSystem files and installed applications will return to this snapshot. Personal files will stay unchanged. No new backup snapshot will be created.')
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        accept = dialog.add_button("Roll Back", Gtk.ResponseType.ACCEPT)
        accept.add_css_class("destructive-action")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        def respond(window, response):
            window.destroy()
            if response == Gtk.ResponseType.ACCEPT:
                self.recover(record["id"])
        dialog.connect("response", respond)
        dialog.present()

    def recover(self, identifier):
        self.work("Restoring your system…", "Keep the computer powered on. This may take a few minutes.",
                  lambda: self.backend.restore(identifier), self.completed)

    def completed(self, _):
        self.clear("Your system has been restored", "Restart to use the restored system. Your personal files were left unchanged.")
        self.body.append(button("Restart", self.restart, True))
        return False

    def failed(self, detail):
        self.clear("Recovery needs attention", "The operation could not be completed. You can retry or restart; an interrupted rollback can be resumed here.")
        details = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                               top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        details.get_buffer().set_text(detail)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(details)
        self.body.append(scroll)
        self.body.append(button("Try Again", self.load, True))
        self.body.append(button("Restart", self.restart))
        return False

    def restart(self):
        self.work("Restarting…", "Please wait.", self.backend.restart, lambda _: False)


def main():
    Gtk.init()
    Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
    css = Gtk.CssProvider()
    css.load_from_string("""
        window { background: #242424; color: #eeeeee; }
        .title-1 { font-size: 28px; font-weight: 700; }
        .heading { font-weight: 700; }
        .boxed-list { border-radius: 14px; background: #303030; }
        .boxed-list row { border-bottom: 1px solid #242424; }
        button { padding: 10px 20px; border-radius: 9px; }
        button.suggested-action { background: #e95420; color: white; }
        .last-boot { color: #92d4a7; font-size: 12px; }
    """)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = Browser()
    try:
        GLib.MainLoop().run()
    finally:
        window.backend.unmount()


if __name__ == "__main__":
    main()
