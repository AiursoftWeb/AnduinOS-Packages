#!/usr/bin/env python3
"""AnduinOS Help Center — application entry point.

The Help Center is a **native** GTK4 + Libadwaita documentation
browser. On startup it kicks off a background job that clones the
official AnduinOS-Docs git repository
(https://github.com/AiursoftWeb/AnduinOS-Docs) and indexes its
Markdown into a local SQLite database. All search, navigation, and
article rendering then read from that local database — fully offline
after the initial sync.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

# Suppress noisy Mesa/EGL warnings *before* anything GTK-related imports.
# These warnings appear in environments where the Zink/EGL stack fails to
# initialise (e.g. headless X, missing GPU drivers, VMs without 3D accel).
# The app still works via the cairo fallback renderer.
os.environ.setdefault("GSK_RENDERER", "ngl")          # ngl is more tolerant than gl
os.environ.setdefault("MESA_LOADER_DRIVER_OVERRIDE", "swrast")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")    # only used if HW fails
os.environ.setdefault("GTK_DEBUG", "no-css")          # silence CSS parser warnings

# Allow running as a script: prepend parent dir to sys.path
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from anduinos_help.docs import sync  # noqa: E402
from anduinos_help.ui.window import HelpWindow  # noqa: E402
from anduinos_help.ui.dialogs import show_about  # noqa: E402
from anduinos_help.utils.logging import get_logger  # noqa: E402
from anduinos_help.utils.paths import ensure_user_dirs  # noqa: E402

APP_ID = "com.anduinos.Help"
APPLICATION_NAME = "AnduinOS Help"

_log = get_logger("anduinos_help.main")


def _load_css() -> None:
    """Load the application stylesheet from the bundled data directory."""
    from anduinos_help.utils.paths import style_css_path
    provider = Gtk.CssProvider()
    css_path = style_css_path()
    if not css_path.exists():
        _log.warning("CSS not found: %s", css_path)
        return
    try:
        provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        _log.info("Loaded CSS: %s", css_path)
    except GLib.Error as exc:
        _log.error("Failed to load CSS: %s", exc)


def _kick_off_background_sync() -> None:
    """Start a background sync if one isn't already running.

    This is fire-and-forget — the UI doesn't wait for it. When the
    sync completes, the in-memory catalog is force-reloaded so any
    new articles appear in the sidebar/home page.
    """
    if sync.is_syncing():
        return
    # Only auto-sync if we've never synced before, or it's been > 6 hours.
    info = sync.last_sync_info()
    if info:
        import datetime as _dt
        try:
            synced_at = _dt.datetime.fromisoformat(info.get("synced_at", ""))
            age = _dt.datetime.now(_dt.timezone.utc) - synced_at
            if age.total_seconds() < 6 * 3600:
                _log.info("Last sync was %s ago — skipping auto-sync", age)
                return
        except (ValueError, OSError):
            pass

    def worker():
        result = sync.sync_blocking()
        if result.success:
            _log.info("Background sync complete: %d documents indexed", result.documents_indexed)
            # Force the loader to re-read the SQLite store next time.
            from anduinos_help.docs import loader
            loader.load_catalog(force=True)
        else:
            _log.warning("Background sync failed: %s", result.error)

    threading.Thread(target=worker, daemon=True, name="docs-sync").start()


class HelpApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._window: HelpWindow | None = None

    def do_activate(self) -> None:
        ensure_user_dirs()
        _load_css()

        if not self._window:
            self._window = HelpWindow(self)
            self._setup_actions()
            self._setup_shortcuts()
            # Kick off a background sync after the window is shown.
            GLib.idle_add(lambda: (_kick_off_background_sync(), False)[1])

        self._window.present()

    def _setup_actions(self) -> None:
        # win.* actions
        def make_action(name: str, callback, accel_keys: list[str] | None = None) -> None:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self._window.add_action(action)
            if accel_keys:
                self.set_accels_for_action(f"win.{name}", accel_keys)

        make_action("go-home",        lambda _a, _p: self._window.go_home(),        ["<Ctrl>H"])
        make_action("diagnostics",    lambda _a, _p: self._window.show_diagnostics(), ["<Ctrl><Shift>D"])
        make_action("report-problem", lambda _a, _p: self._window.report_problem(),  ["<Ctrl><Shift>B"])
        make_action("refresh-docs",   lambda _a, _p: self._window.refresh_docs(),    ["<Ctrl>R", "F5"])
        make_action("glossary",       lambda _a, _p: self._window.show_glossary(),   ["<Ctrl>G"])
        make_action("focus-search",   lambda _a, _p: self._window._home_page.focus_search(), ["<Ctrl>F", "<Ctrl>K"])
        make_action("fullscreen",     lambda _a, _p: self._window.toggle_fullscreen(), ["F11"])
        make_action("shortcuts",      lambda _a, _p: self._window.show_shortcuts(),  ["<Ctrl>question"])

        # app.* actions
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", lambda _a, _p: show_about(self._window))
        self.add_action(about_action)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda _a, _p: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Ctrl>Q"])

    def _setup_shortcuts(self) -> None:
        # AdwApplication automatically wires theme, dark mode, etc.
        pass


def main() -> int:
    try:
        app = HelpApplication()
        return app.run(sys.argv)
    except Exception as exc:  # noqa: BLE001
        _log.error("Fatal error during startup: %s", exc)
        # Show a user-facing error rather than a traceback
        print(f"AnduinOS Help failed to start: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
