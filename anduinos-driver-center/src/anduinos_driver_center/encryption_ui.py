"""Encryption coverage using the same read-only facts as the navigation row."""

import gettext
import json
import re
import secrets
import subprocess
import threading

from gi.repository import Adw, GLib, Gtk

from .encryption import EncryptionState

_ = gettext.gettext
HELPER = "/usr/libexec/anduinos-driver-center/driver-helper"


def encryption_summary(state: EncryptionState) -> str:
    if state.root.encrypted is True:
        return _("Encrypted")
    if state.root.encrypted is False:
        return _("Not encrypted")
    return _("Unknown")


def create_encryption_page(window, state: EncryptionState, recovery):
    page, content = window._page_shell(
        _("Disk Encryption"),
        _("Check encryption for system files, personal files and swap."),
    )
    volumes = Adw.PreferencesGroup(title=_("Encryption coverage"))
    content.append(volumes)
    for title, volume in (
        (_("System files"), state.root),
        (_("Personal files (Home)"), state.home),
    ):
        if volume.encrypted is True:
            description = _("Encrypted")
        elif volume.encrypted is False:
            description = _("Not encrypted")
        else:
            description = _("Unknown")
        if volume.filesystem:
            description += " · " + volume.filesystem
        # Encryption is optional. A plaintext installation is not a fault.
        window._add_state_row(
            volumes, title, description,
            True if volume.encrypted is True else None,
        )

    swap_labels = {
        "encrypted": _("Encrypted"),
        "plaintext": _("Unencrypted disk swap is active"),
        "none": _("No disk swap is active"),
        "unknown": _("Unknown"),
    }
    window._add_state_row(
        volumes, _("Disk swap"), swap_labels[state.disk_swap],
        True if state.disk_swap == "encrypted" else None,
    )
    if any(item.memory for item in state.swaps):
        window._add_state_row(
            volumes, "zram", _("Memory-based swap; not a disk partition"), None,
        )
    if state.root.encrypted is True and state.disk_swap == "plaintext":
        note = Gtk.Label(
            label=_("Unencrypted swap can store personal data from memory."),
            wrap=True, xalign=0,
        )
        note.add_css_class("dim-label")
        content.append(note)

    restore = Adw.PreferencesGroup(title=_("System recovery"))
    content.append(restore)
    if state.root.filesystem == "ext4":
        window._add_state_row(
            restore, _("Snapshot Recovery Environment"),
            _("Not applicable to ext4. Use Rescue Center from the Live USB."),
            None,
        )
    elif state.root.filesystem == "btrfs":
        # These are configuration facts, not evidence of a recovery boot.
        configured = (
            recovery.partition is True
            and recovery.package.installed
            and recovery.grub_entry is True
        )
        window._add_state_row(
            restore, _("Snapshot Recovery Environment"),
            _("Configured") if configured else _("Check recovery configuration"),
            True if configured else None,
            _("Recovery Environment"),
            lambda _button: window._select_page("recovery"),
        )
    else:
        window._add_state_row(
            restore, _("Snapshot Recovery Environment"), _("Unknown"), None,
        )
    if state.root.encrypted is False:
        note = Gtk.Label(
            label=_("Disk encryption can be selected during a new installation."),
            wrap=True, xalign=0,
        )
        note.add_css_class("dim-label")
        content.append(note)
    if state.root.encrypted is True and state.root.filesystem in {"btrfs", "ext4"}:
        management = Adw.PreferencesGroup(title=_("Unlock credentials"))
        content.append(management)
        for role, label in (("password", _("Change disk password")),
                            ("recovery", _("Replace recovery key"))):
            row = Adw.ActionRow(title=label)
            button = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _button, selected=role: CredentialDialog(window, selected).present())
            row.add_suffix(button)
            management.add(row)
    return page


class CredentialDialog(Adw.Window):
    """Keep secrets in this dialog and the existing privileged helper's pipe."""

    def __init__(self, window, role):
        super().__init__(transient_for=window, modal=True, default_width=540)
        self.window = window
        self.role = role
        self.busy = False
        self.legacy = False
        self.ready = False
        self.entries = {}
        self.set_title(_("Change disk password") if role == "password" else _("Replace recovery key"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for edge in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + edge)(24)
        self.set_content(box)
        heading = Gtk.Label(label=self.get_title(), xalign=0)
        heading.add_css_class("title-2")
        box.append(heading)
        self.message = Gtk.Label(label=_("Checking credential configuration…"), wrap=True, xalign=0)
        box.append(self.message)
        self.form = Adw.PreferencesGroup()
        box.append(self.form)
        self.current = self.entry("current", _("Current disk password"))
        self.recovery = self.entry("legacy_recovery", _("Current recovery key"))
        self.recovery.set_visible(False)
        if role == "password":
            self.replacement = self.entry("replacement", _("New disk password"))
            self.confirmation = self.entry("confirmation", _("Confirm new disk password"))
            box.append(Gtk.Label(label=_("Use 8 to 128 printable ASCII characters. The boot screen uses a US keyboard layout."), wrap=True, xalign=0))
        else:
            # Show the generated key before changing the disk, so the user can
            # save it even if the machine stops before the success response.
            self.replacement = Adw.EntryRow(title=_("New recovery key"))
            self.replacement.set_text(secrets.token_hex(24))
            self.replacement.set_editable(False)
            self.form.add(self.replacement)
            self.saved = Gtk.CheckButton(label=_("I have saved the new recovery key outside this computer."))
            self.saved.connect("toggled", lambda *_: self.validate())
            box.append(self.saved)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, halign=Gtk.Align.END)
        box.append(footer)
        self.cancel = Gtk.Button(label=_("Cancel"))
        self.cancel.connect("clicked", lambda *_: self.close())
        footer.append(self.cancel)
        self.apply = Gtk.Button(label=_("Apply"), sensitive=False)
        self.apply.add_css_class("suggested-action")
        self.apply.connect("clicked", self.submit)
        footer.append(self.apply)
        self.connect("close-request", self.close_requested)
        self.request("inspect")

    def entry(self, key, title):
        row = Adw.PasswordEntryRow(title=title)
        self.entries[key] = row
        self.form.add(row)
        row.connect("changed", lambda *_: self.validate())
        return row

    def validate(self):
        if not hasattr(self, "apply"):
            return
        current = self.current.get_text()
        valid = self.ready and bool(re.fullmatch(r"[ -~]{8,128}", current))
        if self.legacy:
            valid = valid and bool(self.recovery.get_text())
        if self.role == "password":
            value = self.replacement.get_text()
            valid = valid and bool(re.fullmatch(r"[ -~]{8,128}", value)) and value == self.confirmation.get_text()
        else:
            valid = valid and self.saved.get_active()
        self.apply.set_sensitive(valid and not self.busy)

    def submit(self, _button):
        self.request("change", {
            "role": self.role,
            "current": self.current.get_text(),
            "replacement": self.replacement.get_text(),
            "legacy_recovery": self.recovery.get_text() if self.legacy else "",
        })

    def request(self, operation, payload=None):
        if self.busy or getattr(self.window, "_operation_running", False):
            self.message.set_label(credential_error("busy"))
            return
        self.busy = True
        self.window._operation_running = True
        self.guard = self.window.connect("close-request", lambda *_: self.busy)
        self.form.set_sensitive(False)
        self.cancel.set_sensitive(False)
        self.apply.set_sensitive(False)
        self.message.set_label(_("Checking credential configuration…") if operation == "inspect" else _("Updating the disk credential. Keep the computer powered on."))
        # Serialized input is never put in argv, environment, an operation log,
        # an exception dialog or a persistent file.
        data = None if payload is None else json.dumps(payload) + "\n"

        def worker():
            try:
                result = subprocess.run(
                    ["pkexec", HELPER, "encryption-" + operation],
                    input=data, capture_output=True, text=True, check=False,
                )
                if result.returncode in (126, 127):
                    response = {"error": "authentication-cancelled"}
                else:
                    response = json.loads(result.stdout)
                    if not isinstance(response, dict) or (result.returncode != 0 and "error" not in response):
                        response = {"error": "operation-failed"}
            except Exception:
                response = {"error": "operation-failed"}
            GLib.idle_add(self.done, operation, response)

        threading.Thread(target=worker, daemon=True).start()

    def done(self, operation, result):
        self.busy = False
        self.window._operation_running = False
        self.window.disconnect(self.guard)
        self.form.set_sensitive(True)
        self.cancel.set_sensitive(True)
        if "error" in result:
            self.message.set_label(credential_error(result["error"]))
            if result["error"] == "setup-required":
                self.legacy = True
                self.recovery.set_visible(True)
            self.validate()
            return GLib.SOURCE_REMOVE
        if operation == "inspect":
            self.ready = True
            self.legacy = not result["managed"]
            self.recovery.set_visible(self.legacy)
            self.message.set_label(
                _("This earlier preview needs the current recovery key once to identify its credential roles.")
                if self.legacy else _("The internal startup credential stays unchanged. The old credential stops working after success.")
            )
            self.validate()
        else:
            self.window._toast(_("Disk password changed.") if self.role == "password" else _("Recovery key replaced."))
            self.clear()
            self.close()
            self.window.refresh()
        return GLib.SOURCE_REMOVE

    def clear(self):
        for entry in self.entries.values():
            entry.set_text("")
        self.replacement.set_text("")

    def close_requested(self, *_):
        if self.busy:
            return True
        self.clear()
        return False


def credential_error(code):
    return {
        "wrong-password": _("The current disk password is incorrect. No credential was changed."),
        "wrong-recovery-key": _("The current recovery key is incorrect. No credential was changed."),
        "setup-required": _("Enter the current recovery key once to identify this preview's credential roles."),
        "credential-reused": _("Choose a new credential that is not already used by this disk."),
        "invalid-request": _("Check the password length, characters and confirmation."),
        "machine-unavailable": _("The internal startup credential could not be verified. Use Rescue Center from the Live USB."),
        "unsupported-layout": _("This credential layout cannot be managed automatically. No existing credential was removed."),
        "layout-changed": _("The disk configuration changed. Stop other disk tools and try again."),
        "busy": _("Another disk credential operation is running. Wait and try again."),
        "authentication-cancelled": _("Administrator authentication was cancelled."),
        "no-free-slot": _("There is no free credential slot. Existing credentials remain available."),
        "verification-failed": _("The new credential could not be verified. Keep your existing credentials and try again."),
    }.get(code, _("The credential operation did not finish. Keep your old and new credentials, then try again using the current disk password."))
