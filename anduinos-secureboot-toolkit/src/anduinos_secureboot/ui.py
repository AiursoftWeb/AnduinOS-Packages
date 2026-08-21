"""Shared GTK/libadwaita Secure Boot trust panel."""

from __future__ import annotations

import subprocess
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .client import run_action
from .inspect import inspect_dkms, inspect_secure_boot
from .model import DkmsState, SecureBootState, SecureBootStatus
from .operations import ENROLLMENT_PASSWORD


Translate = Callable[[str], str]
IconFactory = Callable[[str], Gtk.Widget]


def _default_icon(name: str) -> Gtk.Image:
    return Gtk.Image.new_from_icon_name(name)


def create_secure_boot_page(
    *,
    translate: Translate | None = None,
    icon_factory: IconFactory | None = None,
    update_navigation: Callable[[], None] | None = None,
    reboot: Callable[[], None] | None = None,
    state_changed: Callable[[], None] | None = None,
    initial_state: tuple[SecureBootState, DkmsState] | None = None,
) -> Gtk.Widget:
    """Build the common OOBE-compatible trust page.

    Navigation remains owned by the embedding application. The page owns the
    shared trust rows, action wording, fixed enrollment-code prompt, and repair
    behavior.
    """

    _ = translate or (lambda value: value)
    make_icon = icon_factory or _default_icon
    reboot = reboot or (
        lambda: subprocess.run(
            ["gnome-session-quit", "--reboot", "--no-prompt"], check=False
        )
    )

    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.set_valign(Gtk.Align.FILL)
    page.set_halign(Gtk.Align.FILL)
    page.set_margin_start(48)
    page.set_margin_end(48)
    page.set_margin_top(24)
    page.set_margin_bottom(24)

    center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    center.set_valign(Gtk.Align.CENTER)
    center.set_vexpand(True)
    page.append(center)

    chip_icon = make_icon("secureboot-chip.svg")
    if hasattr(chip_icon, "set_pixel_size"):
        chip_icon.set_pixel_size(72)
    else:
        chip_icon.set_size_request(72, 72)
    chip_icon.set_halign(Gtk.Align.CENTER)
    chip_icon.set_margin_bottom(12)
    center.append(chip_icon)

    title = Gtk.Label(label=_("Secure Boot Configuration"))
    title.add_css_class("title-1")
    title.set_halign(Gtk.Align.CENTER)
    title.set_margin_bottom(8)
    center.append(title)

    description = Gtk.Label(
        label=_(
            "Secure Boot is a motherboard security standard that ensures only trusted software loads at startup."
        )
        + "\n"
        + _("It protects your system from low-level malware and rootkits.")
    )
    description.add_css_class("dim-label")
    description.set_halign(Gtk.Align.CENTER)
    description.set_justify(Gtk.Justification.CENTER)
    description.set_wrap(True)
    description.set_margin_bottom(24)
    center.append(description)

    group = Adw.PreferencesGroup(title=_("System Trust Status"))
    group.set_margin_bottom(24)
    center.append(group)

    rows: dict[str, tuple[Adw.ActionRow, Gtk.Widget]] = {}

    def add_row(key: str, title_text: str) -> None:
        row = Adw.ActionRow(title=title_text)
        icon = _default_icon("dialog-information-symbolic")
        icon.set_pixel_size(16)
        row.add_suffix(icon)
        group.add(row)
        rows[key] = row, icon

    add_row("secure_boot", _("Secure Boot Enabled"))
    add_row("certificate", _("Local MOK Certificate"))
    add_row("enrollment", _("UEFI Firmware Trust"))
    add_row("drivers", _("Third-party Drivers"))

    status = Gtk.Label()
    status.set_justify(Gtk.Justification.CENTER)
    status.set_halign(Gtk.Align.CENTER)
    status.set_wrap(True)
    center.append(status)

    action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    action_box.set_halign(Gtk.Align.CENTER)

    enroll_button = Gtk.Button()
    enroll_button.set_halign(Gtk.Align.CENTER)
    enroll_button.add_css_class("suggested-action")
    enroll_button.add_css_class("pill")
    enroll_button.set_size_request(240, 48)
    enroll_button.set_margin_top(16)
    enroll_spinner = Gtk.Spinner()
    enroll_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    enroll_content.set_halign(Gtk.Align.CENTER)
    enroll_label = Gtk.Label(label=_("Create & Enroll Certificate"))
    enroll_content.append(enroll_spinner)
    enroll_content.append(enroll_label)
    enroll_button.set_child(enroll_content)
    action_box.append(enroll_button)

    repair_button = Gtk.Button(label=_("Repair Module Signatures"))
    repair_button.set_halign(Gtk.Align.CENTER)
    repair_button.add_css_class("suggested-action")
    repair_button.add_css_class("pill")
    repair_button.set_size_request(240, 48)
    action_box.append(repair_button)

    reboot_note = Gtk.Label(
        label=_("Note: Use password <b>123456</b> after rebooting.")
    )
    reboot_note.set_use_markup(True)
    reboot_note.set_halign(Gtk.Align.CENTER)
    action_box.append(reboot_note)
    reboot_button = Gtk.Button(label=_("Reboot & Configure Secure Boot"))
    reboot_button.set_halign(Gtk.Align.CENTER)
    reboot_button.add_css_class("suggested-action")
    reboot_button.add_css_class("pill")
    reboot_button.set_size_request(280, 48)
    action_box.append(reboot_button)
    center.append(action_box)

    refresh_button = Gtk.Button(label=_("  Check Again  "))
    refresh_button.set_halign(Gtk.Align.CENTER)
    refresh_button.set_margin_top(8)
    center.append(refresh_button)

    state_holder: dict[str, SecureBootState | DkmsState | None] = {
        "secure_boot": None,
        "dkms": None,
    }

    def set_icon(key: str, name: str, css_class: str) -> None:
        row, icon = rows[key]
        icon.set_from_icon_name(name)
        for candidate in ("success", "warning", "error", "dim-label"):
            icon.remove_css_class(candidate)
        icon.add_css_class(css_class)

    def apply_state(secure_boot: SecureBootState, dkms: DkmsState) -> bool:
        state_holder["secure_boot"] = secure_boot
        state_holder["dkms"] = dkms
        sb_row, sb_icon = rows["secure_boot"]
        cert_row, cert_icon = rows["certificate"]
        enroll_row, enroll_icon = rows["enrollment"]
        drivers_row, drivers_icon = rows["drivers"]

        if secure_boot.enabled:
            sb_row.set_subtitle(_("Motherboard hardware protection is active"))
            set_icon("secure_boot", "emblem-ok-symbolic", "success")
        elif secure_boot.status is SecureBootStatus.UNSUPPORTED:
            sb_row.set_subtitle(_("Firmware does not support Secure Boot"))
            set_icon("secure_boot", "dialog-information-symbolic", "dim-label")
        elif secure_boot.status is SecureBootStatus.UNKNOWN:
            sb_row.set_subtitle(_("Secure Boot state could not be determined"))
            set_icon("secure_boot", "dialog-error-symbolic", "error")
        else:
            sb_row.set_subtitle(_("Secure Boot is disabled"))
            set_icon("secure_boot", "dialog-information-symbolic", "dim-label")

        has_certificate = secure_boot.key_present and secure_boot.certificate_present
        trust_ready = secure_boot.trust_ready
        if secure_boot.enforcement_inactive:
            cert_row.set_subtitle(_("Not required without firmware enforcement"))
            set_icon("certificate", "dialog-information-symbolic", "dim-label")
            enroll_row.set_subtitle(_("Not required without firmware enforcement"))
            set_icon("enrollment", "dialog-information-symbolic", "dim-label")
        elif secure_boot.status is SecureBootStatus.UNKNOWN:
            cert_row.set_subtitle(
                _("Not checked because Secure Boot state is unknown")
            )
            set_icon("certificate", "dialog-warning-symbolic", "warning")
            enroll_row.set_subtitle(
                _("Not checked because Secure Boot state is unknown")
            )
            set_icon("enrollment", "dialog-warning-symbolic", "warning")
        else:
            cert_row.set_subtitle(
                _("Certificate generated locally")
                if has_certificate
                else _("Missing local certificate")
            )
            set_icon(
                "certificate",
                "emblem-ok-symbolic" if has_certificate else "dialog-warning-symbolic",
                "success" if has_certificate else "warning",
            )

        if (
            not secure_boot.enforcement_inactive
            and secure_boot.status is not SecureBootStatus.UNKNOWN
        ):
            if secure_boot.enrolled:
                enroll_row.set_subtitle(_("Certificate is trusted by motherboard"))
                set_icon("enrollment", "emblem-ok-symbolic", "success")
            elif secure_boot.enrollment_pending:
                enroll_row.set_subtitle(
                    _("Pending enrollment in blue screen (MOKManager)")
                )
                set_icon("enrollment", "dialog-warning-symbolic", "warning")
            else:
                enroll_row.set_subtitle(
                    _("Certificate is not trusted by motherboard")
                )
                set_icon("enrollment", "dialog-error-symbolic", "error")

        signing_configuration_ready = secure_boot.configuration_present
        if secure_boot.enforcement_inactive:
            drivers_row.set_subtitle(_("Kernel signature enforcement is inactive"))
            set_icon("drivers", "dialog-information-symbolic", "dim-label")
        elif secure_boot.status is SecureBootStatus.UNKNOWN:
            drivers_row.set_subtitle(_("Driver trust cannot be verified"))
            set_icon("drivers", "dialog-warning-symbolic", "warning")
        elif not dkms.modules and trust_ready and signing_configuration_ready:
            drivers_row.set_subtitle(
                _("Secure Boot trust is ready. No third-party kernel modules are currently installed.")
            )
            set_icon("drivers", "emblem-ok-symbolic", "success")
        elif dkms.ready and trust_ready and signing_configuration_ready:
            drivers_row.set_subtitle(_("Drivers are signed and ready to load"))
            set_icon("drivers", "emblem-ok-symbolic", "success")
        elif dkms.ready and trust_ready and not signing_configuration_ready:
            drivers_row.set_subtitle(
                _("Drivers are trusted, but automatic DKMS signing needs repair")
            )
            set_icon("drivers", "dialog-warning-symbolic", "warning")
        elif not dkms.modules:
            drivers_row.set_subtitle(_("No signed third-party drivers detected"))
            set_icon("drivers", "dialog-information-symbolic", "dim-label")
        else:
            drivers_row.set_subtitle(_("Some DKMS modules need to be re-signed"))
            set_icon("drivers", "dialog-warning-symbolic", "warning")

        enroll_button.set_visible(secure_boot.enrollment_required)
        enroll_label.set_label(
            _("Enroll Existing Certificate")
            if has_certificate
            else _("Create & Enroll Certificate")
        )
        repair_button.set_visible(
            secure_boot.enabled
            and secure_boot.enrolled
            and (
                not signing_configuration_ready
                or (secure_boot.dkms_available and not dkms.ready)
            )
        )
        repair_button.set_label(
            _("Repair Automatic DKMS Signing")
            if dkms.ready and not signing_configuration_ready
            else _("Repair Module Signatures")
        )
        reboot_note.set_visible(secure_boot.enrollment_pending)
        reboot_button.set_visible(secure_boot.enrollment_pending)
        refresh_button.set_visible(
            secure_boot.status is SecureBootStatus.UNKNOWN
            or (
                secure_boot.enabled
                and (not secure_boot.ready or not dkms.ready)
            )
        )

        if secure_boot.status is SecureBootStatus.UNSUPPORTED:
            status.set_label(
                _(
                    "This firmware does not provide Secure Boot. No certificate is required."
                )
            )
            status.remove_css_class("title-4")
        elif secure_boot.status is SecureBootStatus.UNKNOWN:
            status.set_label(
                _(
                    "Secure Boot status could not be read. Driver trust operations are blocked until detection succeeds."
                )
            )
            status.remove_css_class("title-4")
        elif not secure_boot.enabled:
            status.set_label(_("No certificate is required while Secure Boot is disabled."))
            status.remove_css_class("title-4")
        elif secure_boot.ready and dkms.ready:
            status.set_label(_("System Trust Established. Third-party drivers will load securely."))
            status.add_css_class("title-4")
        elif trust_ready and dkms.ready and not signing_configuration_ready:
            status.set_label(
                _("The certificate is enrolled and current drivers are trusted.")
                + "\n"
                + _("Repair automatic DKMS signing before future driver updates.")
            )
            status.remove_css_class("title-4")
        elif trust_ready:
            status.set_label(
                _("The certificate is enrolled, but some modules are not signed with it.")
            )
            status.remove_css_class("title-4")
        elif secure_boot.enrollment_pending:
            status.set_label(
                _("A certificate is waiting for enrollment.")
                + "\n"
                + _("Restart and enter password 123456 in MOKManager.")
            )
            status.remove_css_class("title-4")
        elif has_certificate:
            status.set_label(
                _("The local trust certificate is not yet enrolled.")
                + "\n"
                + _("You must configure this to use third-party drivers like NVIDIA.")
            )
            status.remove_css_class("title-4")
        else:
            status.set_label(
                _("The local trust certificate is missing.")
                + "\n"
                + _("You must configure this to use third-party drivers like NVIDIA.")
            )
            status.remove_css_class("title-4")
        if state_changed:
            state_changed()
        return GLib.SOURCE_REMOVE

    def inspect_worker() -> None:
        secure_boot = inspect_secure_boot()
        dkms = inspect_dkms(secure_boot)
        GLib.idle_add(apply_state, secure_boot, dkms)
        GLib.idle_add(refresh_button.set_sensitive, True)
        GLib.idle_add(refresh_button.set_label, _("  Check Again  "))

    def refresh(_button: Gtk.Button | None = None) -> None:
        refresh_button.set_sensitive(False)
        refresh_button.set_label(_("  Checking...  "))
        threading.Thread(target=inspect_worker, daemon=True).start()

    def confirm_reboot(_button: Gtk.Button | None = None) -> None:
        dialog = Adw.MessageDialog.new(
            page.get_root(),
            _("Reboot Required"),
            _("Please trust the certificate upon reboot using password 123456."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("reboot", _("Reboot"))
        dialog.set_response_appearance("reboot", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response", lambda _dialog, name: reboot() if name == "reboot" else None
        )
        dialog.present()

    def show_reboot_prompt(extra: str = "") -> None:
        body = _("Success! When you reboot, a blue screen will appear.")
        if extra:
            body += "\n\n" + extra
        body += "\n" + _(
            "Select 'Enroll MOK' → 'Continue' → 'Yes', and enter password: 123456"
        )
        dialog = Adw.MessageDialog.new(
            page.get_root(),
            _("Certificate Created"),
            body,
        )
        dialog.add_response("later", _("Later"))
        dialog.add_response("reboot", _("Reboot & Configure Secure Boot"))
        dialog.set_response_appearance("reboot", Adw.ResponseAppearance.SUGGESTED)

        def response(_dialog: Adw.MessageDialog, name: str) -> None:
            if name == "reboot":
                reboot()

        dialog.connect("response", response)
        dialog.present()

    def action_finished(
        button: Gtk.Button, action: str, code: int, payload: dict
    ) -> bool:
        enroll_spinner.stop()
        enroll_label.set_label(_("Create & Enroll Certificate"))
        button.set_sensitive(True)
        steps = payload.get("steps", {})
        firmware = steps.get("firmware_state", {})
        enrollment = steps.get("enrollment_queued", {}).get("status")
        key_created = steps.get("key_created", {}).get("status")
        trust_prepared = enrollment in {"success", "skipped"} and key_created in {
            "success",
            "skipped",
        }

        if firmware.get("status") == "skipped":
            done = Adw.MessageDialog.new(
                page.get_root(),
                _("Secure Boot Trust Not Required"),
                _("Firmware signature enforcement is not active."),
            )
            done.add_response("ok", _("OK"))
            done.present()
        elif action == "prepare" and trust_prepared:
            page._hide_next = bool(payload.get("reboot_required"))
            if update_navigation:
                update_navigation()
            modules_failed = steps.get("modules_rebuilt", {}).get("status") == "failed"
            warning = (
                _("The certificate is ready, but one or more DKMS modules could not be rebuilt. You can repair them after enrollment.")
                if modules_failed
                else ""
            )
            if payload.get("reboot_required"):
                show_reboot_prompt(warning)
            elif warning:
                failed_dialog = Adw.MessageDialog.new(
                    page.get_root(), _("Configuration needs attention"), warning
                )
                failed_dialog.add_response("ok", _("OK"))
                failed_dialog.present()
            else:
                done = Adw.MessageDialog.new(
                    page.get_root(),
                    _("System Trust Established"),
                    _("Drivers are signed and ready to load"),
                )
                done.add_response("ok", _("OK"))
                done.present()
        elif code == 0:
            done = Adw.MessageDialog.new(
                page.get_root(),
                _("Driver Signature Trusted"),
                _("Drivers are signed and ready to load"),
            )
            done.add_response("ok", _("OK"))
            done.present()
        else:
            detail = payload.get("error")
            if not detail:
                failed = [
                    value.get("detail", "")
                    for value in steps.values()
                    if value.get("status") == "failed"
                ]
                detail = next((item for item in failed if item), "")
            failed_dialog = Adw.MessageDialog.new(
                page.get_root(),
                _("Configuration failed. Please try again."),
                detail or _("Please check the advanced output."),
            )
            failed_dialog.add_response("ok", _("OK"))
            failed_dialog.present()
        refresh()
        return GLib.SOURCE_REMOVE

    def run_in_thread(button: Gtk.Button, action: str) -> None:
        button.set_sensitive(False)
        if action == "prepare":
            enroll_spinner.start()
            enroll_label.set_label(_("Generating & Signing..."))

        def worker() -> None:
            try:
                code, payload = run_action(action)
            except Exception as error:  # UI boundary: present a useful failure.
                code, payload = 1, {"error": str(error)}
            GLib.idle_add(action_finished, button, action, code, payload)

        threading.Thread(target=worker, daemon=True).start()

    enroll_button.connect("clicked", lambda button: run_in_thread(button, "prepare"))
    repair_button.connect(
        "clicked", lambda button: run_in_thread(button, "repair-dkms")
    )
    reboot_button.connect("clicked", confirm_reboot)
    refresh_button.connect("clicked", refresh)
    if initial_state is None:
        refresh()
    else:
        apply_state(*initial_state)
    return page
