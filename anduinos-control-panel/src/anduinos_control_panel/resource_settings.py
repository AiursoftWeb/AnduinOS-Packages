"""Desktop resource protection preferences, applied by the vendor policy helper."""
from __future__ import annotations

import gettext
import json
from pathlib import Path
import re
import subprocess
import threading

from gi.repository import Adw, GLib, Gtk

_ = gettext.gettext
HELPER = '/usr/lib/anduinos-uresourced/helper'
PACKAGE = 'anduinos-uresourced'
PRIORITIES = (100, 300, 500)


class ResourceSettingsWindow(Adw.Window):
    def __init__(self, owner):
        super().__init__(transient_for=owner, modal=True,
                         title=_("Resource Allocation Settings"),
                         default_width=620, default_height=430)
        self.owner = owner
        self.busy = False
        self.loading = False
        self.saved = None
        self.updating = False
        self.reset_pending = False
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_overlay_scrolling(False)
        toolbar.set_content(scroll)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=20, margin_bottom=20, margin_start=24, margin_end=24)
        scroll.set_child(body)
        group = Adw.PreferencesGroup(description=_("Applies to all graphical users on this computer."))
        body.append(group)
        self.enabled = Adw.SwitchRow(title=_("Prioritize resources needed by the desktop (recommended)"))
        self.enabled.connect('notify::active', self._changed)
        group.add(self.enabled)
        self.status = Gtk.Label(wrap=True, xalign=0)
        body.append(self.status)
        self.install = Gtk.Button(label=_("Install"), halign=Gtk.Align.START)
        self.install.connect('clicked', self._install)
        body.append(self.install)
        self.retry = Gtk.Button(label=_("Retry"), halign=Gtk.Align.START)
        self.retry.connect('clicked', lambda _button: self._load())
        body.append(self.retry)
        self.advanced = Gtk.Expander(label=_("Advanced Options"))
        self.advanced.connect('notify::expanded', self._expanded)
        body.append(self.advanced)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.advanced.set_child(details)
        settings = Adw.PreferencesGroup()
        details.append(settings)
        self.memory_row = Adw.ActionRow(
            title=_("Desktop memory protection"),
            subtitle=_("MiB; protection is capped by the shared memory budget, not reserved."))
        self.memory = Gtk.SpinButton.new_with_range(32, 4096, 1)
        self.memory.set_valign(Gtk.Align.CENTER)
        self.memory.set_value(250)
        self.memory.connect('value-changed', self._changed)
        self.memory_row.add_suffix(self.memory)
        settings.add(self.memory_row)
        self.priority = Adw.ComboRow(title=_("Foreground application priority"))
        self.priority.set_model(Gtk.StringList.new([
            _("Normal"), _("Recommended"), _("Higher"), _("Custom (unchanged)")]))
        self.priority.connect('notify::selected', self._changed)
        settings.add(self.priority)
        self.reset = Gtk.Button(label=_("Restore Recommended Settings"), halign=Gtk.Align.START)
        self.reset.connect('clicked', self._reset)
        details.append(self.reset)
        self.notice = Gtk.Label(wrap=True, xalign=0)
        self.notice.add_css_class('dim-label')
        details.append(self.notice)
        output_label = Gtk.Label(label=_("Advanced Output"), xalign=0)
        details.append(output_label)
        self.output = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                   wrap_mode=Gtk.WrapMode.WORD_CHAR)
        output_scroll = Gtk.ScrolledWindow(min_content_height=180,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)
        output_scroll.set_child(self.output)
        details.append(output_scroll)
        self.buffer = self.output.get_buffer()
        footer = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        body.append(footer)
        self.close_button = Gtk.Button(label=_("Close"))
        self.close_button.connect('clicked', lambda _button: self.close_window())
        footer.append(self.close_button)
        self.apply = Gtk.Button(label=_("Apply"))
        self.apply.add_css_class('suggested-action')
        self.apply.connect('clicked', self._apply)
        footer.append(self.apply)
        self._load()

    def close_window(self):
        if not self.busy:
            self.close()

    def _expanded(self, *_arguments):
        self.set_default_size(700, 720) if self.advanced.get_expanded() else self.set_default_size(620, 430)

    def _payload(self):
        if self.saved is None:
            return {}
        result = {}
        previous = self.saved['values']
        if self.enabled.get_active() != previous['enabled']:
            result['enabled'] = self.enabled.get_active()
        if self.memory.get_value_as_int() != self.saved_memory:
            result['memory_mib'] = self.memory.get_value_as_int()
        selected = self.priority.get_selected()
        if selected < len(PRIORITIES):
            value = PRIORITIES[selected]
            if (value, value) != (previous['cpu_weight'], previous['io_weight']):
                result['priority'] = value
        return result

    def _changed(self, *_arguments):
        if not self.updating:
            self._sensitivity()

    def _sensitivity(self):
        readable = self.saved is not None and not self.loading
        usable = readable and not self.busy
        self.enabled.set_sensitive(usable and not self.reset_pending)
        self.memory_row.set_sensitive(usable and self.enabled.get_active() and not self.reset_pending)
        self.priority.set_sensitive(usable and self.enabled.get_active() and not self.reset_pending)
        self.reset.set_sensitive(usable and self.saved['gui_exists'])
        self.retry.set_sensitive(not self.busy and not self.loading)
        self.install.set_sensitive(not self.busy)
        self.close_button.set_sensitive(not self.busy)
        self.set_deletable(not self.busy)
        unhealthy = False
        if readable:
            running = (self.saved['system_active'], self.saved['user_active'])
            unhealthy = not all(running) if self.saved['values']['enabled'] else any(running)
        self.apply.set_sensitive(usable and bool(self.reset_pending or self._payload() or unhealthy))

    def _load(self, message=None):
        self.loading = True
        self.retry.set_visible(False)
        installed = Path(HELPER).is_file()
        self.install.set_visible(not installed)
        if not installed:
            self.loading = False
            self.saved = None
            self.status.set_label(_("Install desktop resource protection to configure it."))
            self._sensitivity()
            return
        self._sensitivity()
        def worker():
            try:
                result = subprocess.run([HELPER, 'status'], capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip())
                GLib.idle_add(self._loaded, json.loads(result.stdout), message)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                GLib.idle_add(self._load_failed, str(error))
        threading.Thread(target=worker, daemon=True).start()

    def _load_failed(self, error):
        self.loading = False
        self.saved = None
        self.retry.set_visible(True)
        self.status.set_label(_("Could not read resource settings: {error}").format(error=error))
        self._sensitivity()

    def _loaded(self, state, message):
        self.loading = False
        self.saved = state
        self.reset_pending = False
        self.updating = True
        values = state['values']
        self.enabled.set_active(values['enabled'])
        # Keep administrator byte/percentage specifications unchanged unless
        # the user actually edits the memory control.
        raw = values['memory']
        match = re.fullmatch(r'(\d+)([KMGT%]?)', raw)
        ram_kib = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                       if line.startswith('MemTotal:'))
        amount, suffix = int(match[1]), match[2]
        mib = amount * ram_kib / 1024 / 100 if suffix == '%' else amount * {
            '': 1 / 1048576, 'K': 1 / 1024, 'M': 1, 'G': 1024, 'T': 1048576}[suffix]
        self.memory.set_range(min(32, mib), max(4096, mib))
        self.memory.set_value(mib)
        self.saved_memory = self.memory.get_value_as_int()
        weights = values['cpu_weight'], values['io_weight']
        selected = PRIORITIES.index(weights[0]) if weights[0] == weights[1] and weights[0] in PRIORITIES else 3
        self.priority.set_selected(selected)
        overrides = any(Path(source).name > '90-anduinos-control-panel.conf'
                        for source in state['sources'].values()
                        if Path(source).parent == Path('/etc/uresourced.conf.d'))
        overrides = overrides or any(source != state['sources'].get(key)
                                    for key, source in state['personal_sources'].items()
                                    if key.startswith('AppBoost.'))
        self.notice.set_label(_("Some choices are overridden by administrator or personal configuration.") if overrides else '')
        self.notice.set_visible(overrides)
        self.owner._append_package_output(self.buffer, self.output,
            json.dumps({'effective': values, 'personal': state['personal'], 'sources': state['sources'],
                        'personal_sources': state['personal_sources']}, ensure_ascii=False, indent=2) + '\n')
        if message:
            self.status.set_label(message)
        elif (values['enabled'] or state['system_active'] or state['user_active']) and not (state['system_active'] and state['user_active']):
            self.status.set_label(_("Protection is enabled, but a resource service is not running."))
        else:
            self.status.set_label(_("Desktop resource protection is enabled.") if state['system_active'] and state['user_active']
                                  else _("Desktop resource protection is disabled."))
        self.updating = False
        self._sensitivity()

    def _reset(self, _button):
        self.reset_pending = True
        self.status.set_label(_("Restore Recommended Settings"))
        self._sensitivity()

    def _run(self, commands):
        self.busy = True
        self.advanced.set_expanded(True)
        self.status.set_label(_("Applying…"))
        self._sensitivity()
        def success():
            self.busy = False
            self._load(_("Resource settings applied."))
        def failure(error):
            self.busy = False
            self.owner._append_package_output(self.buffer, self.output, str(error) + '\n')
            self._load(_("Could not apply settings: {error}").format(error=error))
        self.owner._run_streaming_commands(commands, self.buffer, self.output, success, failure)

    def _apply(self, _button):
        if self.reset_pending:
            arguments = ['reset']
        else:
            payload = self._payload()
            arguments = ['set', json.dumps(payload)] if payload else ['apply']
        self._run([['/usr/bin/pkexec', HELPER, *arguments]])

    def _install(self, _button):
        self._run([['/usr/bin/pkexec', '/usr/bin/apt-get', 'install', '--yes', PACKAGE]])
