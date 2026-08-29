"""Display scaling adaptation based on screen orientation."""

import subprocess
import shutil
from typing import Any

DCONF_APPEARANCE = "/com/anduinos/appearance"
KEY_ENABLED = f"{DCONF_APPEARANCE}/auto-rotate-scale-enabled"
KEY_LANDSCAPE = f"{DCONF_APPEARANCE}/auto-rotate-scale-landscape"
KEY_PORTRAIT = f"{DCONF_APPEARANCE}/auto-rotate-scale-portrait"

SERVICE_NAME = "anduinos-auto-rotatescale.service"

DEFAULT_LANDSCAPE_SCALE = 1.0
DEFAULT_PORTRAIT_SCALE = 1.25

AVAILABLE_SCALES = [
    (1.0, "100%"),
    (1.25, "125%"),
    (1.33, "133%"),
    (1.5, "150%"),
    (1.66, "166%"),
    (1.75, "175%"),
    (2.0, "200%"),
    (2.25, "225%"),
    (2.5, "250%"),
]


def dconf_read(key: str) -> str:
    try:
        res = subprocess.run(
            ["dconf", "read", key],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip().strip("'").strip('"')
    except Exception:
        return ""


def dconf_write(key: str, value: str) -> None:
    subprocess.run(["dconf", "write", key, value], check=True)


def read_rotation_scale_config() -> dict[str, Any]:
    """Read the current auto-rotation scaling configuration."""
    raw_enabled = dconf_read(KEY_ENABLED)
    raw_landscape = dconf_read(KEY_LANDSCAPE)
    raw_portrait = dconf_read(KEY_PORTRAIT)

    try:
        landscape = float(raw_landscape) if raw_landscape else DEFAULT_LANDSCAPE_SCALE
    except ValueError:
        landscape = DEFAULT_LANDSCAPE_SCALE

    try:
        portrait = float(raw_portrait) if raw_portrait else DEFAULT_PORTRAIT_SCALE
    except ValueError:
        portrait = DEFAULT_PORTRAIT_SCALE

    return {
        "enabled": raw_enabled == "true",
        "landscape": landscape,
        "portrait": portrait,
    }


def write_rotation_scale_config(enabled: bool, landscape: float, portrait: float) -> None:
    """Save the rotation scale configuration and manage the systemd user service."""
    dconf_write(KEY_ENABLED, "true" if enabled else "false")
    dconf_write(KEY_LANDSCAPE, str(landscape))
    dconf_write(KEY_PORTRAIT, str(portrait))

    if shutil.which("systemctl"):
        try:
            if enabled:
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", SERVICE_NAME],
                    check=False,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", SERVICE_NAME],
                    check=False,
                    capture_output=True,
                )
        except Exception:
            pass


def is_portrait_transform(transform: int) -> bool:
    """Return True if the Mutter transform corresponds to portrait orientation (90 or 270 degrees)."""
    # 0 = normal (landscape), 1 = 90 deg (portrait), 2 = 180 deg (landscape flipped), 3 = 270 deg (portrait flipped)
    return transform in (1, 3)


def compute_target_scale(transform: int, landscape_scale: float, portrait_scale: float) -> float:
    """Determine the target scaling factor given a monitor transform."""
    return portrait_scale if is_portrait_transform(transform) else landscape_scale


def apply_rotation_scale(iface: Any, landscape_scale: float, portrait_scale: float) -> bool:
    """Query Mutter DisplayConfig, calculate required scales, and apply if different."""
    import dbus

    serial, monitors, logical_monitors, properties = iface.GetCurrentState()
    new_logical = []
    changed = False

    for lm in logical_monitors:
        x, y, current_scale, transform, primary, linked_monitors, props = lm
        target_scale = compute_target_scale(int(transform), landscape_scale, portrait_scale)

        if abs(float(current_scale) - float(target_scale)) > 0.001:
            changed = True
            current_scale = target_scale

        # Mutter expects a list of monitors as (connector, vendor, product, serial)
        new_logical.append((int(x), int(y), float(current_scale), int(transform), bool(primary), linked_monitors))

    if changed:
        # Method 2 = TEMPORARY (Apply immediately without confirmation prompt)
        iface.ApplyMonitorsConfig(dbus.UInt32(serial), dbus.UInt32(2), new_logical, {})
        return True

    return False


def run_watcher() -> None:
    """Main loop for the background rotation scaling daemon."""
    import dbus
    from gi.repository import GLib
    from dbus.mainloop.glib import DBusGMainLoop

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    display_config = bus.get_object("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig")
    iface = dbus.Interface(display_config, "org.gnome.Mutter.DisplayConfig")

    def _sync():
        config = read_rotation_scale_config()
        if not config["enabled"]:
            return
        try:
            apply_rotation_scale(iface, config["landscape"], config["portrait"])
        except Exception as e:
            print(f"Error applying rotation scale: {e}")

    iface.connect_to_signal("MonitorsChanged", lambda: _sync())
    _sync()

    loop = GLib.MainLoop()
    loop.run()
