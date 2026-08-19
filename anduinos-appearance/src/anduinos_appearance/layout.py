"""Shared ArcMenu and Dash-to-Panel layout configuration."""

import json
import subprocess


ARC = "/org/gnome/shell/extensions/arcmenu"
DTP = "/org/gnome/shell/extensions/dash-to-panel"

# (menu layout, forced menu location) for each style and panel position.
MENU_CONFIG = {
    ("classic", "bottom"): ("arcmenu", "BottomLeft"),
    ("classic", "top"): ("arcmenu", "TopLeft"),
    ("classic", "left"): ("arcmenu", "TopLeft"),
    ("classic", "right"): ("arcmenu", "TopRight"),
    ("eleven", "bottom"): ("11", "BottomCentered"),
    ("eleven", "top"): ("11", "TopCentered"),
    ("eleven", "left"): ("11", "Off"),
    ("eleven", "right"): ("11", "Off"),
    ("seperated", "bottom"): ("arcmenu", "BottomLeft"),
    ("seperated", "top"): ("arcmenu", "TopLeft"),
    ("seperated", "left"): ("arcmenu", "TopLeft"),
    ("seperated", "right"): ("arcmenu", "TopRight"),
}

MIN_MENU_HEIGHT = 650
MAX_MENU_HEIGHT = 785
MIN_SCREEN_HEIGHT = 768
MAX_SCREEN_HEIGHT = 1080

MENU_MAX_HEIGHT = {
    "eleven": MIN_MENU_HEIGHT,
    "classic": MAX_MENU_HEIGHT,
    # Seperated uses the same ArcMenu layout as Classic.
    "seperated": MAX_MENU_HEIGHT,
}

POSITIONS = {
    "bottom": "BOTTOM",
    "top": "TOP",
    "left": "LEFT",
    "right": "RIGHT",
}


def _make_panel_element_positions(style: str, monitors: list[str]) -> str:
    """Build Dash-to-Panel's panel-element-positions JSON."""
    if style == "seperated":
        elements = [
            {"element": "centerBox", "visible": True, "position": "stackedTL"},
            {"element": "taskbar", "visible": True, "position": "centerMonitor"},
            {"element": "showAppsButton", "visible": False, "position": "stackedTL"},
            {"element": "activitiesButton", "visible": True, "position": "stackedBR"},
            {"element": "leftBox", "visible": True, "position": "stackedBR"},
            {"element": "rightBox", "visible": True, "position": "stackedBR"},
            {"element": "systemMenu", "visible": True, "position": "stackedBR"},
            {"element": "dateMenu", "visible": True, "position": "stackedBR"},
            {"element": "desktopButton", "visible": True, "position": "stackedBR"},
        ]
    elif style == "eleven":
        elements = [
            {"element": "activitiesButton", "visible": True, "position": "stackedTL"},
            {"element": "showAppsButton", "visible": False, "position": "stackedTL"},
            {"element": "leftBox", "visible": True, "position": "stackedTL"},
            {"element": "centerBox", "visible": True, "position": "stackedBR"},
            {"element": "taskbar", "visible": True, "position": "centerMonitor"},
            {"element": "rightBox", "visible": True, "position": "stackedBR"},
            {"element": "systemMenu", "visible": True, "position": "stackedBR"},
            {"element": "dateMenu", "visible": True, "position": "stackedBR"},
            {"element": "desktopButton", "visible": True, "position": "stackedBR"},
        ]
    else:
        elements = [
            {"element": "centerBox", "visible": True, "position": "stackedTL"},
            {"element": "taskbar", "visible": True, "position": "stackedTL"},
            {"element": "showAppsButton", "visible": False, "position": "stackedTL"},
            {"element": "activitiesButton", "visible": True, "position": "stackedBR"},
            {"element": "leftBox", "visible": True, "position": "stackedBR"},
            {"element": "rightBox", "visible": True, "position": "stackedBR"},
            {"element": "systemMenu", "visible": True, "position": "stackedBR"},
            {"element": "dateMenu", "visible": True, "position": "stackedBR"},
            {"element": "desktopButton", "visible": True, "position": "stackedBR"},
        ]
    return json.dumps({monitor: elements for monitor in monitors})


def dconf_read(key: str) -> str | None:
    """Read one dconf value, returning None when it cannot be read."""
    try:
        result = subprocess.run(
            ["dconf", "read", key], capture_output=True, text=True
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def detect_current() -> tuple[str, str]:
    """Detect the current taskbar style and position."""
    menu_layout = dconf_read(f"{ARC}/menu-layout")
    if menu_layout and "arcmenu" in menu_layout:
        elements = dconf_read(f"{DTP}/panel-element-positions")
        style = "seperated" if elements and "centerMonitor" in elements else "classic"
    else:
        style = "eleven"

    panel_positions = dconf_read(f"{DTP}/panel-positions")
    position = "bottom"
    if panel_positions:
        for candidate, dconf_value in POSITIONS.items():
            if dconf_value in panel_positions:
                position = candidate
                break
    return style, position


def read_group_apps() -> bool:
    value = dconf_read(f"{DTP}/group-apps")
    return value != "false"


def write_group_apps(enabled: bool) -> None:
    subprocess.run(
        ["dconf", "write", f"{DTP}/group-apps", "true" if enabled else "false"],
        check=True,
    )


def read_use_launchers() -> bool:
    return dconf_read(f"{DTP}/group-apps-use-launchers") == "true"


def write_use_launchers(enabled: bool) -> None:
    subprocess.run(
        [
            "dconf",
            "write",
            f"{DTP}/group-apps-use-launchers",
            "true" if enabled else "false",
        ],
        check=True,
    )


def _known_monitors() -> list[str]:
    monitors = ["0", "1", "2"]
    try:
        result = subprocess.run(
            ["dconf", "read", f"{DTP}/panel-anchors"],
            capture_output=True,
            text=True,
            check=True,
        )
        anchors = json.loads(result.stdout.strip().replace("'", '"'))
        for monitor in anchors:
            if monitor not in monitors:
                monitors.append(monitor)
    except Exception:
        pass
    return monitors


def _panel_sizes(monitors: list[str]) -> str:
    try:
        result = subprocess.run(
            ["dconf", "read", f"{DTP}/panel-sizes"],
            capture_output=True,
            text=True,
            check=True,
        )
        existing = json.loads(result.stdout.strip().replace("'", '"'))
        sizes = {monitor: existing.get(monitor, 48) for monitor in monitors}
    except Exception:
        sizes = {monitor: 48 for monitor in monitors}
    return json.dumps(sizes)


def _smallest_monitor_height() -> int | None:
    """Return the smallest monitor's logical height when GDK is available."""
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is None:
            return None

        monitors = display.get_monitors()
        heights = [
            monitors.get_item(index).get_geometry().height
            for index in range(monitors.get_n_items())
        ]
        return min(heights) if heights else None
    except (ImportError, ValueError, AttributeError):
        return None


def calculate_menu_height(style: str, screen_height: int | None = None) -> int:
    """Calculate an ArcMenu height that fits the smallest display.

    Classic-sized menus grow linearly from 650 px at a 768 px-tall display to
    785 px at 1080 px. Values outside that range are clamped. Eleven keeps its
    intentional 650 px maximum.
    """
    if screen_height is None:
        screen_height = _smallest_monitor_height()

    if screen_height is None:
        adaptive_height = MENU_MAX_HEIGHT[style]
    else:
        height_range = MAX_MENU_HEIGHT - MIN_MENU_HEIGHT
        screen_range = MAX_SCREEN_HEIGHT - MIN_SCREEN_HEIGHT
        progress = (screen_height - MIN_SCREEN_HEIGHT) / screen_range
        adaptive_height = round(MIN_MENU_HEIGHT + height_range * progress)
        adaptive_height = max(
            MIN_MENU_HEIGHT, min(MAX_MENU_HEIGHT, adaptive_height)
        )

    return min(MENU_MAX_HEIGHT[style], adaptive_height)


def apply_style_and_position(style: str, position: str) -> bool:
    """Apply one complete taskbar style and position through dconf."""
    menu_layout, force_menu = MENU_CONFIG[(style, position)]
    panel_position = POSITIONS[position]
    monitors = _known_monitors()
    element_positions = _make_panel_element_positions(style, monitors)
    panel_positions = json.dumps(
        {monitor: panel_position for monitor in monitors}
    )
    panel_sizes = _panel_sizes(monitors)
    menu_height = calculate_menu_height(style)

    try:
        subprocess.run(
            ["dconf", "write", f"{DTP}/dot-position", f"'{panel_position}'"],
            check=True,
        )
        subprocess.run(
            ["dconf", "write", f"{DTP}/panel-positions", f"'{panel_positions}'"],
            check=True,
        )
        subprocess.run(
            ["dconf", "write", f"{DTP}/panel-sizes", f"'{panel_sizes}'"],
            check=True,
        )
        subprocess.run(
            [
                "dconf",
                "write",
                f"{DTP}/panel-element-positions",
                f"'{element_positions}'",
            ],
            check=True,
        )
        subprocess.run(
            ["dconf", "write", f"{ARC}/menu-height", str(menu_height)],
            check=True,
        )
        subprocess.run(
            ["dconf", "write", f"{ARC}/force-menu-location", f"'{force_menu}'"],
            check=True,
        )
        subprocess.run(
            ["dconf", "write", f"{ARC}/menu-layout", f"'{menu_layout}'"],
            check=True,
        )
        if style == "eleven":
            subprocess.run(
                ["dconf", "reset", f"{ARC}/menu-arrow-rise"],
                check=True,
            )
            write_group_apps(True)
            write_use_launchers(True)
        else:
            subprocess.run(
                [
                    "dconf",
                    "write",
                    f"{ARC}/menu-arrow-rise",
                    "(true, -8)",
                ],
                check=True,
            )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False
