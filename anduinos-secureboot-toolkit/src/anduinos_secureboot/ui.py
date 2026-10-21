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
    "next-boot-unverified": N_("The next boot entry and kernel have not been verified. Do not enable Secure Boot until the actual boot chain is checked."),
    "firmware-trust-unverified": N_("Firmware trust for the next bootloader has not been verified."),
    "boot-chain-untrusted": N_("The next bootloader is not verified as signed, trusted and deployed."),
    "next-kernel-unverified": N_("The kernel selected for the next boot has not been verified."),
    "next-drivers-unverified": N_("The drivers needed by the next kernel have not been verified."),
}
_ACTION_LABELS = {
    "prepare-identity": N_("Create Signing Certificate"),
    "regenerate-identity": N_("Replace Signing Certificate…"),
    "sync-defaults": N_("Repair Automatic Signing"),
    "repair-dkms": N_("Repair Driver Signatures"),
    "install-bootloader": N_("Install AnduinOS Bootloader"),
    "repair-bootloader": N_("Repair AnduinOS Bootloader"),
    "reboot-enroll": N_("Restart to Enroll Certificate"),
    "firmware-setup": N_("Check Before Enabling Secure Boot"),
    "refresh": N_("Check Again"),
    "show-firmware-help": N_("Firmware Help"),
    "show-boot-recovery": N_("Boot Recovery Help"),
}
_FIRMWARE_INSTRUCTIONS = N_(
    "Secure Boot is configured in UEFI settings. Platform keys and the Microsoft third-party CA "
    "are separate from your MOK certificate. Verify the actual bootloader, next kernel and required "
    "drivers before enabling Secure Boot. Firmware wording varies by manufacturer.")


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
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    header.set_margin_top(8)
    header.set_margin_bottom(16)
    chip = make_icon("secureboot-chip.svg")
    if hasattr(chip, "set_pixel_size"):
        chip.set_pixel_size(48)
    header.append(chip)
    title = Gtk.Label(label=_("Secure Boot Configuration"), xalign=0, wrap=True)
    title.add_css_class("title-1")
    header.append(title)
    page.append(header)
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
        button = Gtk.Button(valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.add_css_class("circular")
        icon = _default_icon("dialog-information-symbolic")
        icon.set_pixel_size(16)
        button.set_child(icon)
        row.add_suffix(button)
        group.add(row)
        rows[key] = (row, icon)
        buttons[key] = button
        button.connect("clicked", lambda _button, row_id=key: show_details(row_id))
    next_step = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    next_step.add_css_class("card")
    next_step.set_margin_top(12)
    next_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    next_content.set_margin_start(24)
    next_content.set_margin_end(24)
    next_content.set_margin_top(20)
    next_content.set_margin_bottom(20)
    next_step.append(next_content)
    next_title = Gtk.Label(label=_("Next step:"), xalign=0)
    next_title.add_css_class("heading")
    next_content.append(next_title)
    status = Gtk.Label(wrap=True, xalign=0)
    status.add_css_class("dim-label")
    next_content.append(status)
    primary_button = Gtk.Button(halign=Gtk.Align.START)
    primary_button.add_css_class("suggested-action")
    primary_button.set_margin_top(8)
    primary_button.set_margin_bottom(4)
    primary_button.connect("clicked", lambda _button: activate(holder["decision"].next_row)
                           if holder["decision"] and holder["decision"].next_row else None)
    next_content.append(primary_button)
    page.append(next_step)
    refresh_button = Gtk.Button(halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
    refresh_button.add_css_class("flat")
    refresh_button.set_child(Adw.ButtonContent(
        icon_name="view-refresh-symbolic", label=_("Check Again")))
    refresh_button.connect("clicked", lambda _button: refresh())
    group.set_header_suffix(refresh_button)
    holder = {"facts": None, "decision": None, "busy": False, "generation": 0}

    def text(reason):
        return _(_MESSAGES[reason])

    def action_label(result):
        if (result.action.id == "prepare-identity" and result.id == "enrollment"
                and result.reason == "certificate-absent"):
            return _("Submit Enrollment Request")
        return _(_ACTION_LABELS[result.action.id])

    def show_details(row_id):
        if holder["busy"] or holder["decision"] is None:
            return
        result = next(r for r in holder["decision"].rows if r.id == row_id)
        if result.tone not in {"warning", "error"} and result.action is None:
            return
        body = text(result.reason)
        target = result if result.action else None
        if result.action and result.action.blockers:
            body += "\n\n" + "\n".join(dict.fromkeys(text(b.reason) for b in result.action.blockers))
            prerequisite = result.action.blockers[0].row_id
            target = next((r for r in holder["decision"].rows
                           if r.id == prerequisite and r.id != row_id and r.action), None)
        if target:
            body += "\n\n" + _("Next step:") + " " + action_label(target)
        dialog = Adw.MessageDialog.new(page.get_root(), rows[row_id][0].get_title(), body)
        dialog.add_response("close", _("Close"))
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        if target:
            dialog.add_response("action", action_label(target))
            dialog.set_response_appearance("action", Adw.ResponseAppearance.SUGGESTED)
            dialog.connect("response", lambda _dialog, choice: activate(target.id)
                           if choice == "action" else None)
        dialog.present()

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
        current = next((r for r in decision.rows if r.id == decision.next_row), None)
        primary_action = current.action.id if current and current.action else None
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
            interactive = result.tone in {"warning", "error"} or result.action is not None
            button.set_can_target(interactive)
            button.set_focusable(interactive)
            button.set_sensitive(not holder["busy"])
            explanation = text(result.reason)
            if result.action and result.action.blockers:
                explanation += "\n\n" + "\n".join(text(b.reason) for b in result.action.blockers)
            button.set_tooltip_text(explanation)
            button.update_property([Gtk.AccessibleProperty.LABEL], [row.get_title() + ": " + explanation])
            if current and result.id == current.id and result.action:
                primary_button.set_label(action_label(result))
                primary_label = primary_button.get_child()
                primary_label.set_wrap(True)
                primary_label.set_max_width_chars(30)
            identity = facts.secure_boot.signing_identity
            detail = (identity.detail if identity and result.id in {"certificate", "certificate_purpose"}
                      else (facts.secure_boot.grub.active_detail or facts.secure_boot.grub.detail)
                      if facts.secure_boot.grub and result.id == "bootloader" else "")
            row.set_tooltip_text(detail or None)
        if decision.next_row:
            result = next(r for r in decision.rows if r.id == decision.next_row)
            status.set_label(text(result.reason))
        else:
            status.set_label(_("No repairs are required by the checks shown above."))
        next_title.set_visible(current is not None)
        primary_button.set_visible(primary_action is not None and primary_action != "refresh")
        primary_button.set_sensitive(not holder["busy"])
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
            def focus_prerequisite(_dialog, choice):
                if choice != "prerequisite":
                    return
                target_action = next(r.action for r in holder["decision"].rows if r.id == target)
                current = next((r for r in holder["decision"].rows
                                if r.id == holder["decision"].next_row), None)
                if current and current.action and target_action and current.action.id == target_action.id:
                    primary_button.grab_focus()
                elif target_action and target_action.id == "refresh":
                    refresh_button.grab_focus()
                else:
                    buttons[target].grab_focus()
            dialog.connect("response", focus_prerequisite)
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
        primary_button.set_sensitive(False)
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
