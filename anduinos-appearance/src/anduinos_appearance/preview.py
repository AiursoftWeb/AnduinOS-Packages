"""Shared Cairo taskbar previews."""

import math


ICON_COLORS = [
    (0.95, 0.45, 0.20),
    (0.25, 0.55, 0.95),
    (0.25, 0.75, 0.45),
    (0.90, 0.25, 0.25),
    (0.85, 0.65, 0.15),
]


def rounded_rect(cr, x, y, w, h, radius):
    cr.new_sub_path()
    cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_icons(cr, x, y, icon_w, icon_h, icon_r, icon_gap, count):
    for index in range(count):
        icon_x = x + index * (icon_w + icon_gap)
        rounded_rect(cr, icon_x, y, icon_w, icon_h, icon_r)
        cr.set_source_rgb(*ICON_COLORS[index % len(ICON_COLORS)])
        cr.fill()


def _draw_start_button(cr, x, y, w, h):
    rounded_rect(cr, x, y, w, h, 3)
    cr.set_source_rgb(0.25, 0.55, 0.95)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    center_x, center_y = x + w / 2, y + h / 2
    size = 3
    cr.set_line_width(1.5)
    cr.move_to(center_x - size, center_y)
    cr.line_to(center_x + size, center_y)
    cr.move_to(center_x, center_y - size)
    cr.line_to(center_x, center_y + size)
    cr.stroke()


def _draw_sys_tray(cr, x, y):
    for index in range(4):
        cr.set_source_rgba(1, 1, 1, 0.45)
        cr.arc(x + index * 10, y + 4, 2.5, 0, 2 * math.pi)
        cr.fill()
    chevron_x = x + 40
    cr.set_source_rgba(1, 1, 1, 0.35)
    cr.set_line_width(1.5)
    cr.move_to(chevron_x, y)
    cr.line_to(chevron_x + 5, y + 5)
    cr.line_to(chevron_x, y + 8)
    cr.stroke()


def draw_preview(area, cr, w, h, style: str, position: str):
    """Draw a desktop preview with the taskbar on the requested edge."""
    del area
    start_centered = style == "eleven"
    icons_centered = style in ("eleven", "seperated")
    bar_thick = 18
    icon_w, icon_h, icon_r, icon_gap, icon_count = 14, 14, 3, 5, 5
    start_w, start_h = 18, 12

    cr.set_source_rgb(0.12, 0.12, 0.14)
    cr.rectangle(0, 0, w, h)
    cr.fill()

    if position == "bottom":
        bar_x, bar_y, bar_w, bar_h = 0, h - bar_thick, w, bar_thick
    elif position == "top":
        bar_x, bar_y, bar_w, bar_h = 0, 0, w, bar_thick
    elif position == "left":
        bar_x, bar_y, bar_w, bar_h = 0, 0, bar_thick, h
    else:
        bar_x, bar_y, bar_w, bar_h = w - bar_thick, 0, bar_thick, h

    horizontal = position in ("bottom", "top")
    cr.set_source_rgba(0.18, 0.18, 0.20, 0.85)
    cr.rectangle(bar_x, bar_y, bar_w, bar_h)
    cr.fill()
    cr.set_source_rgba(1, 1, 1, 0.07)
    cr.set_line_width(1)
    if position == "bottom":
        cr.move_to(bar_x, bar_y)
        cr.line_to(bar_x + bar_w, bar_y)
    elif position == "top":
        cr.move_to(bar_x, bar_y + bar_h)
        cr.line_to(bar_x + bar_w, bar_y + bar_h)
    elif position == "left":
        cr.move_to(bar_x + bar_w, bar_y)
        cr.line_to(bar_x + bar_w, bar_y + bar_h)
    else:
        cr.move_to(bar_x, bar_y)
        cr.line_to(bar_x, bar_y + bar_h)
    cr.stroke()

    if horizontal:
        padding = 6
        start_y = bar_y + 3
        icons_y = bar_y + 2
        icons_width = icon_count * icon_w + (icon_count - 1) * icon_gap
        if start_centered:
            group_width = start_w + 12 + icons_width
            group_x = bar_x + (bar_w - group_width) / 2
            _draw_start_button(cr, group_x, start_y, start_w, start_h)
            _draw_icons(
                cr, group_x + start_w + 12, icons_y,
                icon_w, icon_h, icon_r, icon_gap, icon_count,
            )
        elif icons_centered:
            _draw_start_button(cr, bar_x + padding, start_y, start_w, start_h)
            _draw_icons(
                cr, bar_x + (bar_w - icons_width) / 2, icons_y,
                icon_w, icon_h, icon_r, icon_gap, icon_count,
            )
        else:
            _draw_start_button(cr, bar_x + padding, start_y, start_w, start_h)
            _draw_icons(
                cr, bar_x + padding + start_w + 12, icons_y,
                icon_w, icon_h, icon_r, icon_gap, icon_count,
            )
        _draw_sys_tray(cr, bar_x + bar_w - 52, bar_y + 5)
        cr.set_source_rgba(1, 1, 1, 0.5)
        cr.select_font_face("sans-serif")
        cr.set_font_size(6.5)
        cr.move_to(bar_x + bar_w - 90, bar_y + 14)
        cr.show_text("12:34")
    else:
        start_x, start_y = bar_x + 2, bar_y + 6
        vertical_start_size = bar_thick - 4
        _draw_start_button(
            cr, start_x, start_y, vertical_start_size, vertical_start_size
        )
        vertical_icon_size = bar_thick - 6
        icons_height = (
            icon_count * vertical_icon_size + (icon_count - 1) * icon_gap
        )
        icons_y = (
            bar_y + (bar_h - icons_height) / 2
            if icons_centered
            else bar_y + 30
        )
        for index in range(icon_count):
            icon_y = icons_y + index * (vertical_icon_size + icon_gap)
            rounded_rect(
                cr, bar_x + 3, icon_y,
                vertical_icon_size, vertical_icon_size, icon_r,
            )
            cr.set_source_rgb(*ICON_COLORS[index % len(ICON_COLORS)])
            cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.45)
        cr.select_font_face("sans-serif")
        cr.set_font_size(5.5)
        cr.move_to(bar_x + 2, bar_y + bar_h - 6)
        cr.show_text("12:34")
