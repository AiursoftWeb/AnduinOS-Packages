"""A mapped progress window gets a head start before blocking desktop work."""

import threading

from gi.repository import Gtk, GLib


def run_with_progress(parent, message, task, finished):
    dialog = Gtk.Window(
        transient_for=parent, modal=True, deletable=False, resizable=False,
        title=parent.get_title(), default_width=380,
    )
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    for edge in ('top', 'bottom', 'start', 'end'):
        getattr(content, f'set_margin_{edge}')(28)
    content.append(Gtk.Label(label=message, wrap=True, justify=Gtk.Justification.CENTER))
    bar = Gtk.ProgressBar()
    content.append(bar)
    dialog.set_child(content)

    def complete(error):
        GLib.source_remove(pulse_id)
        try:
            finished(error)
        finally:
            dialog.destroy()
        return GLib.SOURCE_REMOVE

    def worker():
        try:
            task()
            error = None
        except Exception as exc:
            error = exc
        GLib.idle_add(complete, error)

    def start():
        threading.Thread(target=worker, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def mapped(window):
        window.disconnect(map_id)
        # Do not sleep on the main thread: GTK must render the dialog first.
        GLib.timeout_add(200, start)

    def pulse():
        bar.pulse()
        return GLib.SOURCE_CONTINUE

    pulse_id = GLib.timeout_add(80, pulse)
    map_id = dialog.connect('map', mapped)
    dialog.present()
    return dialog
