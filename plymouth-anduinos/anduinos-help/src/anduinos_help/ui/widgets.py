"""Reusable GTK4 + Libadwaita widgets."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, Gdk, GLib, Gtk

from anduinos_help.utils.links import is_internal_link, is_safe_external, open_external, parse_internal_article


def make_link_label(text: str, url: str, on_internal: Any | None = None) -> Gtk.Label:
    """Create a clickable link label.

    External links open in the system browser. Internal links
    (``anduinos-help://article/<id>``) are dispatched to ``on_internal``.
    """
    label = Gtk.Label(label=text)
    label.set_wrap(True)
    label.set_xalign(0)
    if is_internal_link(url):
        label.set_markup(f'<a href="{url}">{GLib.markup_escape_text(text)}</a>')
        if on_internal:
            # Hook via activate-link signal
            def _on_activate(_lbl: Gtk.Label, _uri: str) -> bool:
                aid = parse_internal_article(url)
                if aid:
                    on_internal(aid)
                    return True
                return False
            label.connect("activate-link", _on_activate)
    elif is_safe_external(url):
        label.set_markup(f'<a href="{url}">{GLib.markup_escape_text(text)}</a>')
        label.connect("activate-link", lambda _l, _u: open_external(url) or True)
    else:
        label.set_text(text)
    return label


def make_external_link_button(label: str, url: str) -> Gtk.Button:
    """Create a button that opens an external URL in the system browser."""
    btn = Gtk.Button.new_with_label(label)
    btn.set_tooltip_text(url)
    btn.set_icon_name("external-link-symbolic")
    btn.connect("clicked", lambda _b: open_external(url))
    return btn


def make_callout(kind: str, blocks: list[dict], title: str | None = None, render_block: Any | None = None) -> Gtk.Box:
    """Build a callout box (note/warning/tip/danger)."""
    css_class = {
        "note": "callout-note",
        "tip": "callout-tip",
        "warning": "callout-warning",
        "danger": "callout-danger",
    }.get(kind, "callout-note")
    icon_name = {
        "note": "dialog-information-symbolic",
        "tip": "weather-clear-symbolic",
        "warning": "dialog-warning-symbolic",
        "danger": "dialog-error-symbolic",
    }.get(kind, "dialog-information-symbolic")
    default_title = {
        "note": "Note",
        "tip": "Tip",
        "warning": "Warning",
        "danger": "Danger",
    }.get(kind, "Note")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("callout")
    box.add_css_class(css_class)

    inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    inner.set_margin_top(14)
    inner.set_margin_bottom(14)
    inner.set_margin_start(14)
    inner.set_margin_end(14)

    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(20)
    icon.set_valign(Gtk.Align.START)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    title_label = Gtk.Label(label=title or default_title)
    title_label.add_css_class("callout-title")
    title_label.set_xalign(0)
    title_label.set_hexpand(True)
    content.append(title_label)

    if render_block:
        for blk in blocks:
            content.append(render_block(blk))

    inner.append(icon)
    inner.append(content)
    box.append(inner)
    return box


def make_breadcrumbs(parts: list[tuple[str, str | None]], on_click: Any | None = None) -> Gtk.Box:
    """Build a horizontal breadcrumb trail.

    Each tuple is (label, optional article_id). ``on_click`` is invoked
    with the article_id when set.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    box.add_css_class("breadcrumbs")
    box.set_halign(Gtk.Align.START)
    for i, (label, aid) in enumerate(parts):
        if i > 0:
            sep = Gtk.Label(label="›")
            sep.add_css_class("dim-label")
            sep.set_margin_horizontal(2)
            box.append(sep)
        btn = Gtk.Button.new_with_label(label)
        btn.add_css_class("flat")
        btn.add_css_class("dim")
        btn.set_can_focus(False)
        if aid and on_click:
            btn.connect("clicked", lambda _b, a=aid: on_click(a))
        box.append(btn)
    return box


def empty_state(title: str, description: str, icon_name: str = "system-search-symbolic") -> Gtk.Box:
    """A friendly empty-state widget."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(60)
    box.set_halign(Gtk.Align.CENTER)
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(48)
    icon.add_css_class("dim-label")
    t = Gtk.Label(label=title)
    t.add_css_class("title-2")
    d = Gtk.Label(label=description)
    d.add_css_class("dim-label")
    d.set_wrap(True)
    d.set_max_width_chars(60)
    d.set_halign(Gtk.Align.CENTER)
    box.append(icon)
    box.append(t)
    box.append(d)
    return box


def copy_to_clipboard(text: str) -> None:
    """Copy text to the system clipboard."""
    display = Gdk.Display.get_default()
    if display is None:
        return
    clipboard = display.get_clipboard()
    clipboard.set(text)


def make_copy_button(get_text: Any, tooltip: str = "Copy to clipboard") -> Gtk.Button:
    """Create a small copy button. ``get_text`` is a callable returning str."""
    btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
    btn.set_tooltip_text(tooltip)
    btn.add_css_class("flat")
    btn.set_can_focus(False)

    def on_clicked(_b: Gtk.Button) -> None:
        try:
            text = get_text() if callable(get_text) else str(get_text)
            copy_to_clipboard(text)
            btn.set_icon_name("object-select-symbolic")
            btn.add_css_class("success")
            GLib.timeout_add_seconds(2, _reset)
        except Exception:  # noqa: BLE001
            pass

    def _reset() -> bool:
        btn.set_icon_name("edit-copy-symbolic")
        btn.remove_css_class("success")
        return False

    btn.connect("clicked", on_clicked)
    return btn
