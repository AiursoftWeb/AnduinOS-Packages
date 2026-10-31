"""One Intel page for both in-kernel drivers, with explicit reboot semantics."""
from __future__ import annotations

import gettext
import json
from pathlib import Path
import subprocess
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from .intel_graphics import DRIVERS, PARAMETERS, IntelDevice, IntelSnapshot, can_switch
from .intel_graphics_settings import installed_kernels, status

_ = gettext.gettext
HELPER = "/usr/libexec/anduinos-driver-center/driver-helper"


class IntelGraphicsPage(Gtk.Box):
    def __init__(self, window, snapshot: IntelSnapshot):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.snapshot = snapshot
        self.settings = {}
        self.switches = {}
        self.apply_button = None
        self.rebuild()

    def row(self, group, title, subtitle):
        row = Adw.ActionRow(title=title, subtitle=str(subtitle))
        row.set_use_markup(False)
        group.add(row)
        return row

    def rebuild(self, preserve_draft=False):
        draft_settings = self.settings.copy() if preserve_draft else {}
        draft_switches = self.switches.copy() if preserve_draft else {}
        while self.get_first_child():
            self.remove(self.get_first_child())
        self.settings = {}
        self.switches = {}
        self.initial_switches = {}
        self.apply_button = None
        scroll, content = self.window._page_shell(
            "Intel Graphics", _("Display compatibility settings take effect after restarting."))
        self.append(scroll)
        scroll.set_vexpand(True)
        inventory = Adw.PreferencesGroup(title=_("Graphics"))
        content.append(inventory)
        self.row(inventory, _("Kernel version"), self.snapshot.kernel)
        for device in self.snapshot.devices:
            self.row(inventory, device.model,
                     f"8086:{device.device_id} · {device.address} · {device.driver or _('Not detected')}")
        self.info = {}
        error = ""
        try:
            self.info = status()
        except (OSError, ValueError) as failure:
            error = str(failure)
        tokens = self.info.get("tokens", [])
        if self.info.get("interrupted"):
            self.row(inventory, _("Restore default settings"), _("A previous change was interrupted. Restore defaults before continuing."))
        if self.info.get("pending"):
            self.row(inventory, _("Restart required"), _("Saved settings differ from this boot."))
        conflicts = self.info.get("conflicts", []) + self.info.get("external_boot_parameters", [])
        if conflicts or error:
            self.row(inventory, _("External graphics configuration"), "\n".join(conflicts) or error)

        labels = {
            "enable_psr": _("Panel self refresh (PSR)"),
            "enable_fbc": _("Frame buffer compression (FBC)"),
            "enable_dc": _("Display power-saving states"),
            "enable_panel_replay": _("Panel Replay"),
        }
        active = sorted({device.driver for device in self.snapshot.devices} & set(DRIVERS))
        for driver in active:
            group = Adw.PreferencesGroup(title=driver, description=_(
                "Settings affect all GPUs using this driver. Disabling power-saving features may increase power consumption."))
            content.append(group)
            advanced = Adw.ExpanderRow(title=_("Advanced compatibility settings"))
            for parameter in PARAMETERS:
                key = f"{driver}.{parameter}"
                if parameter not in self.snapshot.parameters.get(driver, []):
                    continue
                # PSR applies to internal panels; Panel Replay can also be supported on external displays.
                if parameter == "enable_psr" and not any(
                    device.driver == driver and device.internal_panel for device in self.snapshot.devices
                ):
                    continue
                value = draft_settings.get(key, "disabled" if f"{key}=0" in tokens else "auto")
                self.settings[key] = value
                row = Adw.ComboRow(title=labels[parameter], model=Gtk.StringList.new([_("Automatic"), _("Disabled")]))
                loaded = self.snapshot.loaded_values.get(key)
                row.set_subtitle(_("Loaded parameter: %s") % (loaded if loaded is not None else _("Unknown")))
                row.set_selected(1 if value == "disabled" else 0)
                row.connect("notify::selected", lambda widget, _spec, name=key: self.select(
                    "settings", name, "disabled" if widget.get_selected() else "auto"))
                if parameter == "enable_psr":
                    group.add(row)
                else:
                    advanced.add_row(row)
            group.add(advanced)
        # Retain settings hidden because a panel is currently disconnected.
        for token in tokens:
            key = token.partition("=")[0]
            if ".force_probe" not in key:
                self.settings.setdefault(key, "disabled")
        switching = Adw.PreferencesGroup(title=_("Kernel driver"))
        content.append(switching)
        kernels = installed_kernels(Path("/"))
        for device in self.snapshot.devices:
            choices = ["default"] + [driver for driver in DRIVERS if can_switch(device, driver, kernels)]
            selected = "default"
            for token in tokens:
                name, separator, ids = token.partition("=")
                if name.endswith(".force_probe") and device.device_id in ids.split(","):
                    selected = name.partition(".")[0]
            self.initial_switches[device.address] = selected
            selected = draft_switches.get(device.address, selected)
            if selected not in choices:
                choices.append(selected)
            self.switches[device.address] = selected
            labels_driver = [_("System default") if choice == "default" else _("Experimental: %s") % choice for choice in choices]
            combo = Adw.ComboRow(title=device.model, model=Gtk.StringList.new(labels_driver))
            combo.set_selected(choices.index(selected))
            combo.set_sensitive(len(choices) > 1)
            combo.connect("notify::selected", lambda row, _spec, address=device.address, values=choices:
                          self.select("switches", address, values[row.get_selected()]))
            switching.add(combo)
        self.row(switching, _("Driver switching"), _("Alternative drivers are available only for hardware and kernels that have passed acceptance tests."))

        diagnosis = Adw.PreferencesGroup(title=_("Diagnostics"))
        content.append(diagnosis)
        self.row(diagnosis, "linux-firmware", self.snapshot.firmware_package or _("Unknown"))
        psr = [f"{key}\n{value}" for key, value in self.snapshot.debug_status.items() if "/PSR" in key]
        self.row(diagnosis, _("PSR status"), "\n".join(psr) or _("Unknown"))
        for firmware in ("GuC", "HuC", "DMC"):
            evidence = [f"{key}\n{value}" for key, value in self.snapshot.debug_status.items() if f"/{firmware}" in key]
            if not evidence:
                evidence = [line for line in self.snapshot.log.splitlines() if firmware.lower() in line.lower()]
            self.row(diagnosis, firmware, "\n".join(evidence[-3:]) or _("Unknown"))
        advice = {
            "psr": _("The boot log reports a panel refresh error. Try disabling PSR and restart."),
            "firmware": _("The boot log reports a firmware error. Check system and firmware updates."),
            "hang": _("The boot log reports a GPU hang or reset. Export diagnostics before changing more settings."),
        }
        for issue in self.snapshot.issues:
            self.row(diagnosis, _("Display troubleshooting"), advice[issue])
        self.row(diagnosis, _("Diagnostic coverage"), _(
            "Module parameters describe policy, not whether a feature is currently active. Missing log evidence does not prove that graphics are healthy."))
        details = Gtk.Button(label=_("Read detailed status"))
        details.connect("clicked", self.read_details)
        content.append(details)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        content.append(footer)
        apply_button = Gtk.Button(label=_("Apply Changes"))
        self.apply_button = apply_button
        self.initial_settings = {key: "disabled" if f"{key}=0" in tokens else "auto" for key in self.settings}
        self.can_apply = bool(active) and not conflicts and not error and not self.info.get("interrupted")
        apply_button.add_css_class("suggested-action")
        self.update_apply()
        apply_button.connect("clicked", self.apply_changes)
        footer.append(apply_button)
        reset = Gtk.Button(label=_("Restore default settings"))
        reset.set_sensitive(bool(tokens or self.info.get("interrupted")) and not error)
        reset.connect("clicked", lambda button: self.window._run_action(
            button, ["intel-reset"], success_message=_("Settings saved. Restart to apply them.")))
        footer.append(reset)
        export = Gtk.Button(label=_("Export diagnostics"))
        export.connect("clicked", self.export)
        content.append(export)

    def select(self, group, key, value):
        getattr(self, group)[key] = value
        self.update_apply()

    def update_apply(self):
        if self.apply_button is not None:
            changed = self.settings != self.initial_settings or self.switches != self.initial_switches
            self.apply_button.set_sensitive(bool(self.can_apply and changed))

    def apply_changes(self, button):
        dialog = Adw.MessageDialog(transient_for=self.window, heading=_("Apply graphics settings?"), body=_(
            "Changes require a restart and may affect display reliability. The boot menu will remain visible for recovery. You can restore defaults from recovery mode.")
            + "\n\n" + _("In recovery mode, sign in with your normal account and run:")
            + "\nsudo /usr/libexec/anduinos-driver-center/driver-helper intel-reset")
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("apply", _("Apply Changes"))
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        payload = json.dumps({"settings": self.settings, "switches": self.switches})
        dialog.connect("response", lambda _dialog, response: self.window._run_action(
            button, ["intel-apply"], stdin=payload, success_message=_("Settings saved. Restart to apply them."))
            if response == "apply" else None)
        dialog.present()

    def read_details(self, button):
        button.set_sensitive(False)
        def worker():
            try:
                result = subprocess.run(["pkexec", HELPER, "intel-inspect"], capture_output=True,
                                        text=True, timeout=120, check=False)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or _("Diagnostic access was not granted."))
                data = json.loads(result.stdout)
                data["devices"] = [IntelDevice(**device) for device in data["devices"]]
                snapshot = IntelSnapshot(**data)
                GLib.idle_add(done, snapshot, "")
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                GLib.idle_add(done, None, str(error))
        def done(snapshot, error):
            button.set_sensitive(True)
            if error:
                self.window._action_error(error)
            else:
                self.snapshot = snapshot
                self.rebuild(preserve_draft=True)
            return GLib.SOURCE_REMOVE
        threading.Thread(target=worker, daemon=True).start()

    def export(self, _button):
        chooser = Gtk.FileChooserNative.new(_("Export diagnostics"), self.window,
                                            Gtk.FileChooserAction.SAVE, _("Save"), _("Cancel"))
        chooser.set_current_name("anduinos-intel-graphics.json")
        def response(dialog, code):
            try:
                if code == Gtk.ResponseType.ACCEPT and dialog.get_file():
                    report = {"graphics": self.snapshot.report(), "configuration": self.info}
                    dialog.get_file().replace_contents(json.dumps(report, ensure_ascii=False, indent=2).encode(),
                                                       None, False, Gio.FileCreateFlags.PRIVATE, None)
            except GLib.Error as error:
                self.window._action_error(str(error))
            finally:
                dialog.destroy()
        chooser.connect("response", response)
        chooser.show()
