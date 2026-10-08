"""GNOME/profile boundaries; leave unrelated extensions and settings alone."""

import time

from .layout import apply_style_and_position, detect_current


PANEL_EXTENSIONS = (
    'dash-to-panel@jderose9.github.com',
    'arcmenu@arcmenu.com',
    'blur-my-shell@aunetx',
)


class ProfileSwitchError(Exception):
    def __init__(self, action, extension=None, *, restart_required=False):
        self.action = action
        self.extension = extension
        self.restart_required = restart_required
        super().__init__(f'{action}: {extension or "layout"}')


def enabled_panel_extensions(settings):
    """Include schema defaults and respect explicitly disabled extensions."""
    enabled = set(settings.get_strv('enabled-extensions'))
    disabled = set(settings.get_strv('disabled-extensions'))
    return set(PANEL_EXTENSIONS) & (enabled - disabled)


def extension_states():
    """Read actual state, not just desired GSettings or CLI exit status."""
    from gi.repository import Gio, GLib

    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = connection.call_sync(
        'org.gnome.Shell.Extensions', '/org/gnome/Shell/Extensions',
        'org.gnome.Shell.Extensions', 'ListExtensions',
        None, GLib.VariantType.new('(a{sa{sv}})'),
        Gio.DBusCallFlags.NONE, 5000, None,
    )
    return reply.unpack()[0]


def wait_for_extensions(enabled=None, timeout=30):
    """Wait for Shell's asynchronous rebase, including other extensions.

    Only ACTIVE (1) means enabled; INACTIVE (2), INITIALIZED (6) or a missing
    extension means disabled. ERROR (3) is NOT a successful disable. Require
    a stable interval as D-Bus replies can precede settings processing.
    """
    deadline = time.monotonic() + timeout
    stable_since = None
    while True:
        states = extension_states()
        for uuid in PANEL_EXTENSIONS:
            state = states.get(uuid, {}).get('state')
            if state in (3, 4):
                raise ProfileSwitchError('state', uuid, restart_required=state == 3)
        idle = not any(info.get('state') in (7, 8) for info in states.values())
        matched = enabled is None or all(
            (states.get(uuid, {}).get('state') == 1 if enabled else
             states.get(uuid, {}).get('state') in (None, 2, 6))
            for uuid in PANEL_EXTENSIONS
        )
        now = time.monotonic()
        if idle and matched:
            if stable_since is None:
                stable_since = now
            if now - stable_since >= 0.3:
                return states
        else:
            stable_since = None
        if now >= deadline:
            raise ProfileSwitchError('timeout')
        time.sleep(0.05)  # Only runs in the progress worker, never GTK's thread.


def detect_profile(settings):
    style, position = detect_current()
    if not enabled_panel_extensions(settings):
        style = 'gnome'
    return style, position


def requires_extension_changes(style, enabled):
    if style == 'gnome':
        return bool(enabled)
    return set(enabled) != set(PANEL_EXTENSIONS)


def apply_profile(style, position, screen_height=None, *, settings=None):
    """Run off the GTK thread; configure the layout before enabling extensions.

    Change one extension-list key per phase. CLI enable/disable writes two
    keys and returns before Shell's asynchronous rebase finishes, permitting
    overlapping disable()/enable() calls. A single disabled-list update lets
    Shell choose its own reverse activation order safely.
    """
    if style not in ('gnome', 'classic', 'separated', 'eleven'):
        raise ValueError(f'Unknown profile: {style}')
    from gi.repository import Gio

    if settings is None:
        settings = Gio.Settings.new('org.gnome.shell')

    def write(key, values):
        if values == settings.get_strv(key):
            return
        if not settings.set_strv(key, values):
            raise ProfileSwitchError('settings')
        Gio.Settings.sync()

    states = wait_for_extensions()
    if style == 'gnome':
        disabled = settings.get_strv('disabled-extensions')
        write('disabled-extensions', disabled + [
            uuid for uuid in PANEL_EXTENSIONS if uuid not in disabled
        ])
        wait_for_extensions(False)
        return

    for uuid in PANEL_EXTENSIONS:
        if uuid not in states:
            raise ProfileSwitchError('enable', uuid)
    if not apply_style_and_position(style, position, screen_height=screen_height):
        raise ProfileSwitchError('layout')

    # Previous versions removed UUIDs from enabled-extensions. Restore missing
    # UUIDs while masked, so the preparatory write cannot activate extensions.
    enabled = settings.get_strv('enabled-extensions')
    missing = [uuid for uuid in PANEL_EXTENSIONS if uuid not in enabled]
    if missing:
        disabled = settings.get_strv('disabled-extensions')
        write('disabled-extensions', disabled + [uuid for uuid in missing if uuid not in disabled])
        wait_for_extensions()
        write('enabled-extensions', enabled + missing)
        wait_for_extensions()

    disabled = settings.get_strv('disabled-extensions')
    write('disabled-extensions', [uuid for uuid in disabled if uuid not in PANEL_EXTENSIONS])
    wait_for_extensions(True)
