"""Sidebar navigation: lists all documentation categories."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, Gtk

from anduinos_help.docs import loader
from anduinos_help.i18n import _
from anduinos_help.system import version


class Sidebar(Gtk.Box):
    """A Libadwaita-style navigation sidebar with grouped categories."""

    # Keys that route to a dialog rather than a category page
    SPECIAL_KEYS = {"diagnostics", "glossary", "about", "report-problem", "check-updates"}

    def __init__(
        self,
        on_select_category: Any | None = None,
        on_home: Any | None = None,
        on_special: Any | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._on_select_category = on_select_category
        self._on_home = on_home
        self._on_special = on_special

        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.set_margin_start(6)
        self.set_margin_end(6)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.add_css_class("navigation-sidebar")
        self._list.connect("row-selected", self._on_row_selected)
        self.append(self._list)
        self.populate()

    def populate(self) -> None:
        # Home entry
        self._add_row("home", _("Home"), "go-home-symbolic", is_action=True)
        self._add_separator()

        cat = loader.load_catalog()
        for category in loader.categories_sorted():
            count = len(cat.by_category(category))
            if count == 0:
                continue
            label = loader.CATEGORY_DISPLAY.get(category, category.title())
            icon = CATEGORY_ICONS.get(category, "text-x-generic-symbolic")
            self._add_row(
                key=category,
                label=label,
                icon=icon,
                badge=str(count),
            )

        self._add_separator()
        self._add_row("check-updates", _("Check for Updates"), "system-software-update-symbolic")
        self._add_row("diagnostics", _("System Diagnostics"), "applications-system-symbolic")
        self._add_row("report-problem", _("Report a Problem"), "dialog-warning-symbolic")
        self._add_row("glossary", _("Glossary"), "accessories-dictionary-symbolic")
        self._add_row("about", _("About"), "help-about-symbolic")

    def _add_row(self, key: str, label: str, icon: str, badge: str | None = None, is_action: bool = False) -> None:
        row = Gtk.ListBoxRow()
        row.row_key = key
        row.is_action = is_action

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        inner.set_margin_top(9)
        inner.set_margin_bottom(9)
        inner.set_margin_start(10)
        inner.set_margin_end(10)

        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(18)
        inner.append(img)

        lbl = Gtk.Label(label=label)
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        inner.append(lbl)

        if badge:
            b = Gtk.Label(label=badge)
            b.add_css_class("dim-label")
            b.add_css_class("caption")
            b.set_xalign(1)
            inner.append(b)

        row.set_child(inner)
        self._list.append(row)

    def _add_separator(self) -> None:
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_child(sep)
        # Mark as separator
        row.row_key = None
        self._list.append(row)

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if not row:
            return
        key = getattr(row, "row_key", None)
        if key is None:
            return
        if key == "home":
            if self._on_home:
                self._on_home()
        elif key in self.SPECIAL_KEYS:
            if self._on_special:
                self._on_special(key)
        elif self._on_select_category:
            self._on_select_category(key)

    def select_home(self) -> None:
        """Programmatically select the Home entry."""
        first = self._list.get_row_at_index(0)
        if first:
            self._list.select_row(first)


CATEGORY_ICONS: dict[str, str] = {
    "getting-started": "go-home-symbolic",
    "install": "system-software-install-symbolic",
    "desktop": "computer-symbolic",
    "applications": "applications-system-symbolic",
    "system": "emblem-system-symbolic",
    "hardware": "audio-card-symbolic",
    "networking": "network-wireless-symbolic",
    "terminal": "utilities-terminal-symbolic",
    "troubleshooting": "tools-check-spelling-symbolic",
    "security": "preferences-system-privacy-symbolic",
    "developers": "applications-development-symbolic",
    "virtualization": "application-x-extension-vmdk-symbolic",
    "servicing": "folder-remote-symbolic",
    "skills": "preferences-system-symbolic",
    "reference": "x-office-document-symbolic",
    "releases": "view-task-symbolic",
}
