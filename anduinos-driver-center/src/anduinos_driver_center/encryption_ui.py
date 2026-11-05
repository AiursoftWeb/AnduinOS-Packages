"""Encryption coverage using the same read-only facts as the navigation row."""

import gettext

from gi.repository import Adw, Gtk

from .encryption import EncryptionState

_ = gettext.gettext


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
    return page
