"""Per-user Bash prediction preferences and optional package installation."""
import gettext
import importlib.util
from pathlib import Path
import threading

from gi.repository import Adw, GLib, Gtk
from .model import package_installed

try:
    from . import _prediction_config as config
except ImportError:
    source = Path(__file__).resolve().parents[3] / 'lib/bash_prediction_settings.py'
    spec = importlib.util.spec_from_file_location('_prediction_config', source)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

_ = gettext.gettext
PACKAGE = 'anduinos-bash-guess-command'


class PredictionSettingsWindow(Adw.Window):
    def __init__(self, owner):
        super().__init__(transient_for=owner, modal=True,
                         title=_("Bash Command Predictions"), default_width=650, default_height=650)
        self.owner = owner
        self.updating = False
        self.settings_readable = False
        self.busy = False
        self.installed = package_installed(PACKAGE)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_overlay_scrolling(False)
        toolbar.set_content(scroll)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=20, margin_bottom=20, margin_start=24, margin_end=24)
        scroll.set_child(body)
        intro = Adw.PreferencesGroup(
            title=_("Bash Command Predictions"),
            description=_("Predict commands locally from your history and current context. No model download or on-device LLM is needed."))
        body.append(intro)
        self.install_row = Adw.ActionRow(title=_("Not installed"),
                                         subtitle=_("Configure your preferences below, then install to use predictions."))
        self.install_button = Gtk.Button(label=_("Install"), valign=Gtk.Align.CENTER)
        self.install_button.add_css_class('suggested-action')
        self.install_button.connect('clicked', self._install)
        self.install_row.add_suffix(self.install_button)
        intro.add(self.install_row)
        self.install_row.set_visible(not self.installed)
        group = Adw.PreferencesGroup(description=_("Changes take effect in newly opened Bash terminals. Saved settings override legacy environment variables."))
        body.append(group)
        definitions = (
            ('enabled', _("Enable command predictions"), _("Show suggestions while typing in Bash.")),
            ('history', _("Learn from command history"), _("Use Bash history, frequency, directories, and command sequences to personalize suggestions.")),
            ('persist', _("Remember my usage habits"), _("Save learned usage habits on this computer for predictions after closing the terminal. When off, Bash command history is still used.")),
        )
        self.switches = {}
        for key, title, subtitle in definitions:
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            row.connect('notify::active', self._changed, key)
            group.add(row)
            self.switches[key] = row
        operations = Adw.PreferencesGroup()
        body.append(operations)
        clear_row = Adw.ActionRow(title=_("Clear saved learning"),
                                 subtitle=_("Keeps Bash history and your settings. Reopen existing terminals to discard their in-memory learning."))
        self.clear_button = Gtk.Button(label=_("Clear"), valign=Gtk.Align.CENTER)
        self.clear_button.connect('clicked', self._confirm_clear)
        clear_row.add_suffix(self.clear_button)
        operations.add(clear_row)
        reset_row = Adw.ActionRow(title=_("Restore default settings"), subtitle=_("Does not delete learned data."))
        self.reset_button = Gtk.Button(label=_("Restore Defaults"), valign=Gtk.Align.CENTER)
        self.reset_button.connect('clicked', self._reset)
        reset_row.add_suffix(self.reset_button)
        operations.add(reset_row)
        hint = Gtk.Label(label=_("At the end of a line, press Right Arrow or End to accept a suggestion. Press Enter to run the command."),
                         wrap=True, xalign=0)
        hint.add_css_class('dim-label')
        body.append(hint)
        self.status = Gtk.Label(wrap=True, xalign=0)
        body.append(self.status)
        self.output_expander = Gtk.Expander(label=_("Advanced Output"))
        self.output = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        output_scroll = Gtk.ScrolledWindow(min_content_height=150, hscrollbar_policy=Gtk.PolicyType.NEVER)
        output_scroll.set_child(self.output)
        self.output_expander.set_child(output_scroll)
        self.output_expander.set_visible(False)
        body.append(self.output_expander)
        self._load()

    def _load(self):
        try:
            values = config.read_settings()
        except (OSError, ValueError) as error:
            self._error(error)
            # Do not overwrite unreadable user configuration with guessed values.
            self.settings_readable = False
            self._sensitivity()
            return
        self.settings_readable = True
        self.updating = True
        for key, row in self.switches.items():
            row.set_active(values[key])
        self.updating = False
        self._sensitivity()

    def _sensitivity(self):
        for row in self.switches.values():
            row.set_sensitive(not self.busy and self.settings_readable)
        self.switches['persist'].set_sensitive(not self.busy and self.settings_readable and self.switches['history'].get_active())
        self.clear_button.set_sensitive(not self.busy)
        self.reset_button.set_sensitive(not self.busy)
        self.install_button.set_sensitive(not self.busy)
        self.set_deletable(not self.busy)

    def _error(self, error):
        self.status.set_label(_("Could not update prediction settings: {error}").format(error=error))

    def _changed(self, row, _property, key):
        if self.updating:
            return
        values = {name: item.get_active() for name, item in self.switches.items()}
        try:
            config.save_settings(values)
        except (OSError, ValueError) as error:
            self._error(error)
        self._load()

    def _reset(self, _button):
        try:
            config.restore_defaults()
            self.status.set_label(_("Default settings restored. Reopen Bash terminals to apply them."))
            self._load()
        except OSError as error:
            self._error(error)

    def _confirm_clear(self, _button):
        dialog = Adw.MessageDialog(transient_for=self, heading=_("Clear saved learning?"),
                                  body=_("Only prediction learning is removed. Your Bash command history is kept."))
        dialog.add_response('cancel', _("Cancel"))
        dialog.add_response('clear', _("Clear"))
        dialog.set_response_appearance('clear', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', lambda _dialog, response: self._clear() if response == 'clear' else None)
        dialog.present()

    def _clear(self):
        self.busy = True
        self._sensitivity()
        def work():
            error = None
            try:
                config.clear_learning()
            except OSError as exc:
                error = exc
            GLib.idle_add(done, error)
        def done(error):
            self.busy = False
            self._sensitivity()
            if error:
                self._error(error)
            else:
                self.status.set_label(_("Saved learning cleared. Older terminals cannot save it again; reopen them to resume learning persistence."))
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()

    def _install(self, _button):
        if package_installed(PACKAGE):
            self.installed = True
            self.install_row.set_visible(False)
            return
        self.busy = True
        self._sensitivity()
        self.status.set_label(_("Administrator authentication is required when installation starts."))
        self.output_expander.set_visible(True)
        self.output_expander.set_expanded(True)
        def completed():
            self.busy = False
            self.installed = package_installed(PACKAGE)
            self.install_row.set_visible(not self.installed)
            self._sensitivity()
            self.status.set_label(_("Installation complete. Open a new Bash terminal to use predictions.") if self.installed else _("Package installation could not be confirmed. Please retry."))
            self.owner._rebuild_categories()
        def failed(message):
            self.busy = False
            self._sensitivity()
            self.status.set_label(_("Installation failed: {error}").format(error=message))
        self.owner._run_streaming_package_change(PACKAGE, self.output.get_buffer(), self.output, completed, failed)
