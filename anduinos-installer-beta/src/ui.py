"""Shared visual components for the native installer."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gtk


_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_ICON_DIRS = (
    _SOURCE_ROOT / "assets" / "icons",
    Path("/usr/share/anduinos-installer-beta/icons"),
)
_STYLE_FILES = (
    _SOURCE_ROOT / "assets" / "style.css",
    Path("/usr/share/anduinos-installer-beta/style.css"),
)


def load_visual_style(display: Gdk.Display | None = None) -> None:
    """Load the package-local stylesheet once for the current display."""

    display = display or Gdk.Display.get_default()
    if display is None:
        return
    style_file = next((path for path in _STYLE_FILES if path.is_file()), None)
    if style_file is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(str(style_file))
    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def icon_path(name: str) -> Path | None:
    filename = name if name.endswith(".svg") else f"{name}.svg"
    return next(
        (
            directory / filename
            for directory in _ICON_DIRS
            if (directory / filename).is_file()
        ),
        None,
    )


def icon_picture(name: str, size: int) -> Gtk.Picture:
    picture = Gtk.Picture(
        width_request=size,
        height_request=size,
        content_fit=Gtk.ContentFit.CONTAIN,
        can_shrink=True,
        halign=Gtk.Align.CENTER,
        valign=Gtk.Align.CENTER,
    )
    path = icon_path(name)
    if path is not None:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path), size, size, True
        )
        picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
    else:
        picture.set_paintable(
            Gtk.IconTheme.get_for_display(picture.get_display()).lookup_icon(
                name,
                None,
                size,
                1,
                Gtk.TextDirection.NONE,
                Gtk.IconLookupFlags.PRELOAD,
            )
        )
    return picture


def page_hero(title: str, subtitle: str, icon: str) -> Gtk.Box:
    """Create the shared magazine-style heading for a wizard page."""

    hero = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=20,
        valign=Gtk.Align.CENTER,
    )
    hero.add_css_class("installer-hero")

    emblem = Gtk.Box(
        halign=Gtk.Align.CENTER,
        valign=Gtk.Align.CENTER,
    )
    emblem.add_css_class("installer-hero-icon")
    emblem.append(icon_picture(icon, 62))

    copy = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=5,
        hexpand=True,
        valign=Gtk.Align.CENTER,
    )
    title_label = Gtk.Label(
        label=title,
        halign=Gtk.Align.START,
        xalign=0,
        wrap=True,
    )
    title_label.add_css_class("installer-hero-title")
    subtitle_label = Gtk.Label(
        label=subtitle,
        halign=Gtk.Align.START,
        xalign=0,
        wrap=True,
    )
    subtitle_label.add_css_class("installer-hero-subtitle")
    copy.append(title_label)
    copy.append(subtitle_label)
    hero.append(emblem)
    hero.append(copy)
    hero._icon_box = emblem
    hero._title_label = title_label
    hero._subtitle_label = subtitle_label
    return hero


def clamp_content(child: Gtk.Widget, maximum_size: int = 920) -> Adw.Clamp:
    clamp = Adw.Clamp(maximum_size=maximum_size, tightening_threshold=720)
    clamp.set_child(child)
    return clamp


def card(*, spacing: int = 12) -> Gtk.Box:
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=spacing,
    )
    box.add_css_class("installer-card")
    return box
