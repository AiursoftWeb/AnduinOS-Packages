#!/usr/bin/env python3
"""Verify source changes against an isolated real GNOME/Wayland compositor.

Run with python3 tests/integration/keyboard_wayland.py. Requires GNOME Shell
with headless support. No packages are installed and the real user's dconf,
extensions, runtime directory and session bus are not used.
"""

import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time

PACKAGE = Path(__file__).resolve().parents[2]


def session():
    root = Path(os.environ['XDG_RUNTIME_DIR']).parent
    assert os.environ.get('ANDUINOS_KEYBOARD_WAYLAND_TEST') == '1'
    assert root.name.startswith('anduinos-keyboard-wayland-')
    assert root.stat().st_uid == os.getuid()
    assert Path(os.environ['XDG_CONFIG_HOME']) == root / 'config'
    assert Path(os.environ['XDG_DATA_HOME']) == root / 'data'
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gio, GLib
    from keyboard_session import apply_keyboard_layout, keyboard_layout_is_active

    settings = Gio.Settings.new('org.gnome.shell')
    settings.set_boolean('disable-user-extensions', True)
    settings.set_strv('enabled-extensions', [])
    Gio.Settings.sync()
    log_path = Path(os.environ['XDG_RUNTIME_DIR']).parent / 'shell.log'
    with log_path.open('w+') as log:
        shell = subprocess.Popen(
            ['gnome-shell', '--headless', '--wayland', '--no-x11',
             '--virtual-monitor=1280x800', '--wayland-display=anduinos-keyboard-test'],
            stdout=log, stderr=subprocess.STDOUT)
        window = None
        try:
            socket = Path(os.environ['XDG_RUNTIME_DIR']) / 'anduinos-keyboard-test'
            deadline = time.monotonic() + 20
            while not socket.exists():
                if shell.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError('headless compositor did not start')
                time.sleep(0.05)
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            def call(path, interface, method, parameters=None):
                return connection.call_sync(
                    'org.gnome.Mutter.RemoteDesktop', path, interface, method,
                    parameters, None, Gio.DBusCallFlags.NONE, 5000, None).unpack()
            remote_interface = 'org.gnome.Mutter.RemoteDesktop'
            deadline = time.monotonic() + 15
            while True:
                try:
                    remote_path = call('/org/gnome/Mutter/RemoteDesktop', remote_interface, 'CreateSession')[0]
                    break
                except GLib.Error:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.05)
            remote_interface += '.Session'
            call(remote_path, remote_interface, 'Start')
            # Headless Mutter has no hardware keyboard. A virtual keyboard is
            # needed before Wayland clients can receive the actual seat keymap.
            for pressed in (True, False):
                call(remote_path, remote_interface, 'NotifyKeyboardKeycode', GLib.Variant('(ub)', (42, pressed)))
            # GTK's automatic initialization must happen after the socket exists.
            from gi.repository import Gtk, Gdk
            Gtk.init()
            window = Gtk.Window(title='Isolated installer keyboard verification')
            window.set_child(Gtk.Entry())
            window.present()
            context = GLib.MainContext.default()
            for layout, variant in [('us', ''), ('ch', ''), ('fr', ''),
                                    ('us', 'intl'), ('jp', ''), ('ch', 'de_nodeadkeys')]:
                signature = apply_keyboard_layout(layout, variant)
                deadline = time.monotonic() + 10
                while True:
                    while context.pending():
                        context.iteration(False)
                    display = Gdk.Display.get_default()
                    if keyboard_layout_is_active(display, signature):
                        print(f'GNOME/Wayland: {layout}+{variant or "default"} passed', flush=True)
                        break
                    if time.monotonic() > deadline:
                        stored = Gio.Settings.new('org.gnome.desktop.input-sources')
                        print('Stored sources:', stored.get_value('sources').unpack(), flush=True)
                        for code, expected in signature.items():
                            found, keys, symbols = display.map_keycode(code)
                            actual = {key.level: sym for key, sym in zip(keys, symbols) if key.group == 0}
                            if any(actual.get(level) != sym for level, sym in expected.items()):
                                print('Keymap mismatch:', code, expected, actual, flush=True)
                        raise AssertionError('compositor keymap did not match selection')
                    time.sleep(0.025)

            # Exercise the actual installer page without mocking its session
            # writer, GDK verification, or key event handling.
            import pages
            from gi.repository import Adw

            def descendants(widget):
                yield widget
                child = widget.get_first_child()
                while child is not None:
                    yield from descendants(child)
                    child = child.get_next_sibling()

            def wait(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    while context.pending():
                        context.iteration(False)
                    if predicate():
                        return
                    time.sleep(0.02)
                raise AssertionError('native keyboard input did not reach expected GTK state')

            navigation = Adw.NavigationView()
            page = pages.build_keyboard_page(
                {'lang': 'en_US', 'keyboard': 'ch', 'keyboard_variant': '',
                 '_page_route_initialized': True, '_network_page_planned': False},
                navigation)
            navigation.add(page)
            window.set_child(navigation)
            entry = next(w for w in descendants(page) if isinstance(w, Gtk.Entry))
            wait(lambda: entry.get_sensitive() and window.is_active())
            entry.grab_focus()

            def dollar():
                for pressed in (True, False):
                    # RemoteDesktop uses Linux evdev codes, without XKB's +8.
                    call(remote_path, remote_interface, 'NotifyKeyboardKeycode',
                         GLib.Variant('(ub)', (43, pressed)))

            dollar()
            wait(lambda: entry.get_text() == '$')
            entry.select_region(0, -1)
            dollar()
            wait(lambda: entry.get_text() == '$' and not entry.get_selection_bounds())
            password = Gtk.PasswordEntry()
            window.set_child(password)
            password.grab_focus()
            wait(lambda: window.is_active())
            dollar()
            wait(lambda: password.get_text() == '$')
            print('GNOME/Wayland: actual installer tester, selection replacement and password input passed', flush=True)
        except Exception:
            log.flush()
            print(log_path.read_text()[-10000:], file=sys.stderr)
            raise
        finally:
            if window is not None:
                window.destroy()
            shell.terminate()
            try:
                shell.wait(timeout=5)
            except subprocess.TimeoutExpired:
                shell.kill()
                shell.wait()


if __name__ == '__main__':
    if sys.argv[1:] == ['--isolated-session']:
        session()
    else:
        with tempfile.TemporaryDirectory(prefix='anduinos-keyboard-wayland-') as directory:
            root = Path(directory)
            for name in ('config', 'data', 'cache', 'runtime'):
                (root / name).mkdir(mode=0o700)
            environment = {
                **os.environ,
                'PYTHONPATH': os.pathsep.join((str(PACKAGE / 'src'),
                                             str(PACKAGE.parent / 'anduinos-secureboot-toolkit/src'))),
                'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONWARNINGS': 'ignore::DeprecationWarning',
                'XDG_CONFIG_HOME': str(root / 'config'),
                'XDG_DATA_HOME': str(root / 'data'),
                'XDG_CACHE_HOME': str(root / 'cache'),
                'XDG_RUNTIME_DIR': str(root / 'runtime'),
                'GSETTINGS_BACKEND': 'dconf',
                'GDK_BACKEND': 'wayland',
                'WAYLAND_DISPLAY': 'anduinos-keyboard-test',
                'LIBGL_ALWAYS_SOFTWARE': '1',
                'NO_AT_BRIDGE': '1',
                'ANDUINOS_KEYBOARD_WAYLAND_TEST': '1',
            }
            process = subprocess.Popen(
                ['dbus-run-session', '--', sys.executable, str(Path(__file__).resolve()), '--isolated-session'],
                env=environment, start_new_session=True)
            try:
                result = process.wait(timeout=110)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
            raise SystemExit(result)
