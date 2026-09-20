"""Explicit read-only export from an already running voice session."""
import sys


def main():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    from . import APP_ID, INTERFACE, OBJECT_PATH
    from .diagnostics import sanitize_report
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        response = connection.call_sync(
            APP_ID, OBJECT_PATH, INTERFACE, "GetDiagnostics", None,
            GLib.VariantType("(s)"), Gio.DBusCallFlags.NO_AUTO_START, 3000, None,
        )
    except GLib.Error:
        print("No compatible running voice session. Start dictation, then export before closing it.",
              file=sys.stderr)
        return 1
    try:
        print(sanitize_report(response.unpack()[0]))
    except (ValueError, TypeError):
        print("The voice session returned an invalid diagnostic report.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
