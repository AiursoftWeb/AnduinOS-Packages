"""Category page: lists all articles in a category."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, Gtk

from anduinos_help.docs import loader
from anduinos_help.ui.widgets import empty_state


class CategoryPage(Gtk.Box):
    """A scrollable page listing all articles in a given category."""

    def __init__(self, on_navigate_article: Any | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_navigate_article = on_navigate_article

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_hexpand(True)
        self._scroller.set_vexpand(True)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._content.set_margin_top(34)
        self._content.set_margin_bottom(60)
        self._content.set_margin_start(56)
        self._content.set_margin_end(56)
        self._content.set_halign(Gtk.Align.CENTER)
        self._content.set_hexpand(True)
        self._scroller.set_child(self._content)
        self.append(self._scroller)

    def render(self, category: str) -> None:
        self._clear()
        cat = loader.load_catalog()
        display = loader.CATEGORY_DISPLAY.get(category, category.title())

        title = Gtk.Label(label=display)
        title.set_xalign(0)
        title.add_css_class("title-1")
        self._content.append(title)

        count = len(cat.by_category(category))
        subtitle = Gtk.Label(label=f"{count} article{'s' if count != 1 else ''}")
        subtitle.set_xalign(0)
        subtitle.add_css_class("dim-label")
        subtitle.add_css_class("title-4")
        self._content.append(subtitle)

        subcats = cat.subcategories(category)
        if subcats:
            for sub in subcats:
                sub_title = Gtk.Label(label=sub)
                sub_title.set_xalign(0)
                sub_title.add_css_class("title-3")
                sub_title.set_margin_top(12)
                self._content.append(sub_title)

                lb = Gtk.ListBox()
                lb.add_css_class("boxed-list")
                lb.set_selection_mode(Gtk.SelectionMode.NONE)
                for art in cat.by_subcategory(category, sub):
                    lb.append(self._make_row(art))
                self._content.append(lb)
        else:
            lb = Gtk.ListBox()
            lb.add_css_class("boxed-list")
            lb.set_selection_mode(Gtk.SelectionMode.NONE)
            for art in cat.by_category(category):
                lb.append(self._make_row(art))
            self._content.append(lb)

        if count == 0:
            self._content.append(empty_state(
                "No articles yet",
                "There is no bundled documentation for this category. Try updating from Settings.",
                icon_name="folder-documents-symbolic",
            ))

    def _make_row(self, art: loader.Article) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.set_title(art.title)
        row.set_subtitle(art.summary)
        badges = Gtk.Label(label=art.display_category)
        badges.add_css_class("dim-label")
        badges.add_css_class("caption")
        row.add_suffix(badges)
        arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
        arrow.add_css_class("dim-label")
        row.add_suffix(arrow)
        row.set_activatable(True)
        row.connect("activated", lambda _r, aid=art.id: self._navigate(aid))
        return row

    def _clear(self) -> None:
        child = self._content.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._content.remove(child)
            child = nxt

    def _navigate(self, article_id: str) -> None:
        if self._on_navigate_article:
            self._on_navigate_article(article_id)
