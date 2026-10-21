"""GTK presentation and explicit effects for the pure Secure Boot decisions."""
from __future__ import annotations

import subprocess
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .client import run_action
from .decision import Facts, assess_action, decide
from .enrollment import EnrollmentTutorial
from .inspect import collect_facts

Translate = Callable[[str], str]
IconFactory = Callable[[str], Gtk.Widget]


def N_(value: str) -> str:
    return value


_MESSAGES = {
    "secure-boot-enabled": N_("Motherboard Secure Boot is enabled."),
    "secure-boot-prepared": N_("Preparation checks passed. Secure Boot is still disabled."),
    "secure-boot-disabled": N_("Secure Boot is disabled. You can still prepare certificates, drivers and the bootloader."),
    "firmware-unknown": N_("Secure Boot state could not be determined. Check again before changing trust."),
    "firmware-unsupported": N_("Secure Boot is not available in the current firmware or boot mode."),
    "platform-keys-missing": N_("Firmware platform keys are not configured. This is separate from MOK enrollment."),
    "identity-unknown": N_("The signing identity could not be checked. Existing certificates have not been replaced."),
    "identity-missing": N_("Create a local signing certificate before preparing trust."),
    "identity-valid": N_("The signing certificate and private key are valid and match."),
    "signing-key-unavailable": N_("The signing key is missing, unreadable or does not match its certificate. Existing signatures are unchanged."),
    "certificate-unified": N_("This certificate can sign both the bootloader and kernel modules."),
    "certificate-module-only": N_("This certificate can sign drivers only. A new certificate is needed for the AnduinOS bootloader."),
    "certificate-expired": N_("The certificate has expired. Create and enroll a replacement for future signing."),
    "certificate-unsupported": N_("The certificate does not permit the required signing. Create and enroll a replacement."),
    "certificate-enrolled": N_("This certificate is enrolled in MOK."),
    "certificate-pending": N_("Enrollment requested. Restart and select Enroll MOK to finish trusting this certificate."),
    "certificate-absent": N_("This certificate is not enrolled. Submit an enrollment request."),
    "enrollment-unknown": N_("MOK enrollment could not be verified. Check again before continuing."),
    "signing-defaults-required": N_("The automatic signing configuration needs attention. Another certificate enrollment is not needed."),
    "signing-defaults-unknown": N_("The automatic signing configuration has not been verified. Check again before installing the bootloader."),
    "drivers-unknown": N_("Third-party drivers could not be checked. This does not mean no drivers are installed."),
    "drivers-none": N_("No third-party kernel modules need attention."),
    "drivers-trusted": N_("Installed drivers are trusted, including signatures from previously enrolled certificates."),
    "drivers-untrusted": N_("One or more third-party drivers need a trusted signature."),
    "bootloader-unsupported": N_("The UEFI bootloader operation is not available in the current boot mode."),
    "bootloader-optional": N_("The AnduinOS bootloader is not installed. Installing it is recommended."),
    "bootloader-unknown": N_("The installed bootloader could not be checked."),
    "bootloader-ready": N_("AnduinOS GRUB is signed, trusted and deployed."),
    "bootloader-unsigned": N_("The AnduinOS bootloader needs signing or its signature could not be verified."),
    "bootloader-unenrolled": N_("The bootloader is signed, but its certificate is not enrolled."),
    "bootloader-undeployed": N_("The signed bootloader needs deployment to this system's EFI partition."),
    "boot-recovery-required": N_("The installed EFI boot chain could not be verified as trusted. Restore a trusted boot path before restarting."),
    "firmware-unavailable": N_("Check the firmware and boot mode before continuing."),
    "signing-certificate-unavailable": N_("Prepare a valid signing certificate first."),
    "unified-certificate-required": N_("Create a certificate that can sign both the bootloader and drivers first."),
    "certificate-enrollment-required": N_("The signing certificate must be enrolled first. Complete the certificate enrollment step above."),
    "driver-build-unavailable": N_("DKMS and the matching kernel headers are required. Check the driver build requirements."),
}
_ACTION_LABELS = {
    "prepare-identity": N_("Create Signing Certificate"),
    "regenerate-identity": N_("Replace Signing Certificate…"),
    "sync-defaults": N_("Repair Automatic Signing"),
    "repair-dkms": N_("Repair Driver Signatures"),
    "install-bootloader": N_("Install AnduinOS Bootloader"),
    "repair-bootloader": N_("Repair AnduinOS Bootloader"),
    "reboot-enroll": N_("Restart to Enroll Certificate"),
    "firmware-setup": N_("Open Firmware Settings"),
    "refresh": N_("Check Again"),
    "show-firmware-help": N_("Firmware Help"),
    "show-boot-recovery": N_("Boot Recovery Help"),
}
_FIRMWARE_INSTRUCTIONS = N_(
    "Restart to UEFI settings, enable Secure Boot, then save your changes and start AnduinOS. "
    "Firmware wording varies by manufacturer. Platform keys and the Microsoft third-party CA "
    "are separate from your MOK certificate. The preparation checks shown here do not replace "
    "a successful boot with Secure Boot enabled.")


def restart_to_firmware_settings(run=subprocess.run) -> tuple[bool, str]:
    try:
        result = run(["systemctl", "reboot", "--firmware-setup"], capture_output=True,
                     text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    return (True, "") if result.returncode == 0 else (False, (result.stderr or result.stdout).strip())


def _default_icon(name: str):
    return Gtk.Image.new_from_icon_name(name)


def _bootloader_result_dialog(parent, code, payload, translate):
    _ = translate
    dialog = Adw.MessageDialog.new(parent,
        _("Bootloader Updated") if code == 0 else _("Bootloader Update Failed"),
        _("The bootloader operation finished. The installed files will now be checked again.")
        if code == 0 else _("The bootloader update could not be completed. Review the details before restarting."))
    detail = "\n".join(filter(None, [payload.get("error", ""), *[
        step.get("detail", "") for step in payload.get("steps", {}).values()]]))
    if detail:
        log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                           wrap_mode=Gtk.WrapMode.WORD_CHAR)
        log.get_buffer().set_text(detail)
        scroll = Gtk.ScrolledWindow(height_request=180,
            hscrollbar_policy=Gtk.PolicyType.NEVER, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(log)
        expander = Gtk.Expander(label=_("Details"), expanded=code != 0)
        expander.set_child(scroll)
        dialog.set_extra_child(expander)
    dialog.add_response("ok", _("OK"))
    return dialog


def create_secure_boot_page(*, translate=None, icon_factory=None, reboot=None,
                            firmware_setup=None, state_changed=None, initial_state=None,
                            initial_facts: Facts | None = None):
    _ = translate or (lambda value: value)
    make_icon = icon_factory or _default_icon
    reboot = reboot or (lambda: subprocess.run(["gnome-session-quit", "--reboot", "--no-prompt"], check=False))
    firmware_setup = firmware_setup or restart_to_firmware_settings
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    page.set_margin_start(24)
    page.set_margin_end(24)
    page.set_margin_top(24)
    page.set_margin_bottom(24)
    chip = make_icon("secureboot-chip.svg")
    if hasattr(chip, "set_pixel_size"):
        chip.set_pixel_size(64)
    page.append(chip)
    title = Gtk.Label(label=_("Secure Boot Configuration"))
    title.add_css_class("title-1")
    page.append(title)
    group = Adw.PreferencesGroup(title=_("System Trust Status"))
    page.append(group)
    rows, buttons = {}, {}
    for key, text in (
        ("secure_boot", _("Secure Boot")), ("certificate", _("Local MOK Certificate")),
        ("certificate_purpose", _("Certificate Signing Purpose")),
        ("enrollment", _("Certificate Enrollment")), ("drivers", _("Third-party Drivers")),
        ("bootloader", _("AnduinOS Bootloader")),
    ):
        row = Adw.ActionRow(title=text)
        button = Gtk.Button(label=_("Check Again"))
        button.set_valign(Gtk.Align.CENTER)
        label = button.get_child()
        if label is not None:
            label.set_wrap(True)
            label.set_width_chars(18)
            label.set_max_width_chars(24)
        icon = _default_icon("dialog-information-symbolic")
        icon.set_pixel_size(16)
        row.add_suffix(button)
        row.add_suffix(icon)
        group.add(row)
        rows[key] = (row, icon)
        buttons[key] = button
        button.connect("clicked", lambda _button, row_id=key: activate(row_id))
    status = Gtk.Label()
    status.set_wrap(True)
    status.set_xalign(0)
    page.append(status)
    refresh_button = Gtk.Button(halign=Gtk.Align.END)
    refresh_button.add_css_class("flat")
    refresh_button.set_child(Adw.ButtonContent(
        icon_name="view-refresh-symbolic", label=_("Check Again")))
    refresh_button.connect("clicked", lambda _button: refresh())
    page.append(refresh_button)
    holder = {"facts": None, "decision": None, "busy": False, "generation": 0}

    def text(reason):
        return _(_MESSAGES[reason])

    def add_enrollment_tutorial(dialog, body):
        dialog.set_extra_child(EnrollmentTutorial(body))
        width = page.get_root().get_width()
        dialog.set_default_size(min(720, max(320, width - 48)), -1)

    def show_message(title, body, enrollment=False):
        dialog = Adw.MessageDialog.new(page.get_root(), title, body)
        if enrollment:
            add_enrollment_tutorial(dialog, body)
        dialog.add_response("ok", _("OK"))
        dialog.present()
        return GLib.SOURCE_REMOVE

    def draw(facts, generation=None):
        if generation is not None and generation != holder["generation"]:
            return GLib.SOURCE_REMOVE
        holder["facts"] = facts
        decision = holder["decision"] = decide(facts)
        for result in decision.rows:
            row, icon = rows[result.id]
            row.set_subtitle(text(result.reason))
            row.set_visible(True)
            icon.set_from_icon_name({"success": "emblem-ok-symbolic", "warning": "dialog-warning-symbolic",
                                     "error": "dialog-error-symbolic", "neutral": "dialog-information-symbolic"}[result.tone])
            for css in ("success", "warning", "error", "dim-label"):
                icon.remove_css_class(css)
            icon.add_css_class("dim-label" if result.tone == "neutral" else result.tone)
            button = buttons[result.id]
            button.set_visible(result.action is not None)
            button.set_sensitive(not holder["busy"])
            if result.action:
                label = _(_ACTION_LABELS[result.action.id])
                if (result.action.id == "prepare-identity" and result.id == "enrollment"
                        and result.reason == "certificate-absent"):
                    label = _("Submit Enrollment Request")
                button.set_label(label)
            identity = facts.secure_boot.signing_identity
            detail = (identity.detail if identity and result.id in {"certificate", "certificate_purpose"}
                      else (facts.secure_boot.grub.active_detail or facts.secure_boot.grub.detail)
                      if facts.secure_boot.grub and result.id == "bootloader" else "")
            row.set_tooltip_text(detail or None)
        if decision.next_row:
            result = next(r for r in decision.rows if r.id == decision.next_row)
            status.set_label(_("Next step:") + " " + text(result.reason))
        elif decision.rows[0].reason == "secure-boot-prepared":
            status.set_label(text("secure-boot-prepared"))
        else:
            status.set_label(_("No repairs are required by the checks shown above."))
        refresh_button.set_sensitive(not holder["busy"])
        if state_changed:
            state_changed()
        return GLib.SOURCE_REMOVE

    def collect():
        return collect_facts(recover=True)

    def refresh():
        if holder["busy"]:
            return
        holder["generation"] += 1
        generation = holder["generation"]
        refresh_button.set_sensitive(False)
        def worker():
            try:
                facts = collect()
            except Exception as error:
                GLib.idle_add(show_message, _("Inspection Failed"), str(error))
                GLib.idle_add(refresh_button.set_sensitive, True)
                return
            GLib.idle_add(draw, facts, generation)
        threading.Thread(target=worker, daemon=True).start()

    def blocked(action):
        dialog = Adw.MessageDialog.new(page.get_root(), _("Complete This Step First"),
                                      "\n\n".join(text(b.reason) for b in action.blockers))
        dialog.add_response("cancel", _("Close"))
        target = action.blockers[0].row_id
        # A local inspection/manual issue has no earlier row to navigate to.
        if target != "secure_boot" or action.id != "firmware-setup":
            dialog.add_response("prerequisite", _("Go to This Step"))
            dialog.connect("response", lambda _dialog, choice: buttons[target].grab_focus()
                           if choice == "prerequisite" else None)
        dialog.present()

    def confirm(title, body, response_id, response_label, callback, enrollment=False):
        dialog = Adw.MessageDialog.new(page.get_root(), title, body)
        if enrollment:
            add_enrollment_tutorial(dialog, body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response(response_id, response_label)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance(response_id, Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _dialog, choice: callback() if choice == response_id else None)
        dialog.present()

    def finish(progress, action, code, payload):
        holder["busy"] = False
        progress.close()
        if action in {"install-bootloader", "repair-bootloader"}:
            _bootloader_result_dialog(page.get_root(), code, payload, _).present()
        else:
            detail = "\n".join(filter(None, [payload.get("error", ""), *[
                step.get("detail", "") for step in payload.get("steps", {}).values()]]))
            if code == 0 and payload.get("reboot_required"):
                detail = (_("A certificate is waiting for enrollment. Restart and select Enroll MOK, Continue, Yes, then enter password 123456.")
                          + "\n\n" + _("Existing bootloader, driver signatures and old enrolled certificates are preserved."))
            show_message(_("Operation Finished") if code == 0 else _("Operation Failed"),
                         detail or _("The system will now be checked again."),
                         enrollment=code == 0 and bool(payload.get("reboot_required")))
        refresh()
        return GLib.SOURCE_REMOVE

    def start_operation(action):
        if holder["busy"]:
            return
        holder["busy"] = True
        holder["generation"] += 1  # Discard probes started before this operation.
        for button in buttons.values():
            button.set_sensitive(False)
        refresh_button.set_sensitive(False)
        progress = Adw.MessageDialog.new(page.get_root(), _("Working…"),
                                        _("Please wait. Do not restart while this operation is running."))
        progress.set_extra_child(Gtk.Spinner(spinning=True))
        progress.set_deletable(False)
        progress.present()
        def worker():
            try:
                latest = collect()
                preflight = assess_action(action, latest)
                if not preflight.allowed:
                    code, payload = 1, {"error": "\n".join(text(b.reason) for b in preflight.blockers)}
                else:
                    code, payload = run_action("repair-defaults" if action == "sync-defaults" else action)
            except Exception as error:
                code, payload = 1, {"error": str(error)}
            GLib.idle_add(finish, progress, action, code, payload)
        def launch():
            threading.Thread(target=worker, daemon=True).start()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(200, launch)

    def restart_action(action):
        if holder["busy"]:
            return
        holder["busy"] = True
        def worker():
            try:
                latest = collect()
                decision = assess_action(action, latest)
                if not decision.allowed:
                    GLib.idle_add(blocked, decision)
                elif action == "firmware-setup":
                    ok, error = firmware_setup()
                    if not ok:
                        GLib.idle_add(show_message, _("Firmware Settings Failed"), error)
                else:
                    GLib.idle_add(reboot)
            except Exception as error:
                GLib.idle_add(show_message, _("Inspection Failed"), str(error))
            finally:
                GLib.idle_add(reset_busy)
        threading.Thread(target=worker, daemon=True).start()

    def reset_busy():
        holder["busy"] = False
        refresh()
        return GLib.SOURCE_REMOVE

    def activate(row_id):
        if holder["busy"] or holder["decision"] is None:
            return
        result = next(r for r in holder["decision"].rows if r.id == row_id)
        action = result.action
        if action is None:
            return
        if not action.allowed:
            blocked(action)
        elif action.id == "refresh":
            refresh()
        elif action.id == "show-firmware-help":
            show_message(_("Firmware Configuration"), _(_FIRMWARE_INSTRUCTIONS))
        elif action.id == "show-boot-recovery":
            show_message(_("Restore a Trusted Boot Path"),
                         _("Do not restart until the installed EFI boot files are trusted. Restore a known-good EFI backup or use trusted installation media to repair them. Keep existing enrolled certificates."))
        elif action.id in {"reboot-enroll", "firmware-setup"}:
            body = (_("Select Enroll MOK, Continue, Yes, and enter password 123456 on restart.")
                    if action.id == "reboot-enroll" else _(_FIRMWARE_INSTRUCTIONS))
            confirm(_("Restart Required"), body, "reboot", _("Restart"), lambda: restart_action(action.id),
                    enrollment=action.id == "reboot-enroll")
        else:
            body = (_("The bootloader will be signed with your enrolled certificate and deployed. Existing EFI files are backed up. Keep the computer running until the operation finishes.")
                    if action.id in {"install-bootloader", "repair-bootloader"} else
                    _("This updates the selected signing configuration. Creating or replacing a certificate preserves existing boot and driver trust. A new certificate requires confirmation in MOKManager after restart."))
            confirm(_(_ACTION_LABELS[action.id]), body, "continue", _("Continue"),
                    lambda: start_operation(action.id))

    if initial_facts is not None:
        draw(initial_facts)
    elif initial_state is not None:
        draw(Facts(*initial_state, drivers_checked=True))
    else:
        refresh()
    return page
