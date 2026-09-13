"""Live operation output; closing a window must not interrupt package installation."""
import gettext

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk

_ = gettext.gettext


class OperationWindow(Adw.Window):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title=_("Working…"))
        self.set_default_size(760, 500)
        self.running = True
        self.connect('close-request', lambda *_: self.running)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        layout.append(header)
        self.status = Gtk.Label(label=_("Working…"), wrap=True)
        self.status.set_margin_start(20)
        self.status.set_margin_end(20)
        layout.append(self.status)
        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_start(20)
        self.progress.set_margin_end(20)
        layout.append(self.progress)
        self.log = Gtk.TextView(editable=False, cursor_visible=False,
                                monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log.set_left_margin(12)
        self.log.set_right_margin(12)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.log)
        layout.append(scroll)
        self.close_button = Gtk.Button(label=_("OK"), sensitive=False)
        self.close_button.set_margin_start(20)
        self.close_button.set_margin_end(20)
        self.close_button.set_margin_bottom(20)
        self.close_button.connect('clicked', lambda *_: self.close())
        layout.append(self.close_button)
        self.set_content(layout)
        self._pulse = GLib.timeout_add(100, self._tick)

    def _tick(self):
        self.progress.pulse()
        return GLib.SOURCE_CONTINUE

    def append(self, text):
        buffer = self.log.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        # Bound the visible log, including programs emitting very long lines.
        if buffer.get_char_count() > 200000:
            buffer.delete(buffer.get_start_iter(), buffer.get_iter_at_offset(buffer.get_char_count() - 150000))
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.log.scroll_mark_onscreen(mark)
        buffer.delete_mark(mark)
        return GLib.SOURCE_REMOVE

    def finish(self, code, message):
        self.running = False
        GLib.source_remove(self._pulse)
        self.progress.set_fraction(1.0 if code == 0 else 0.0)
        self.status.set_label(message)
        self.set_title(_("Driver operation failed: ").rstrip(': ') if code else _("Driver changes completed. Restart may be required."))
        if code:
            self.status.add_css_class('error')
        self.close_button.set_sensitive(True)
