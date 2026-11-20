"""Search results page."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, GLib, Gtk

from anduinos_help.docs import search as search_engine
from anduinos_help.i18n import _
from anduinos_help.ui.widgets import empty_state


class SearchPage(Gtk.Box):
    """A scrollable page displaying search results."""

    def __init__(self, on_navigate_article: Any | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_navigate_article = on_navigate_article

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_hexpand(True)
        self._scroller.set_vexpand(True)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._content.set_margin_top(30)
        self._content.set_margin_bottom(60)
        self._content.set_margin_start(56)
        self._content.set_margin_end(56)
        self._content.set_halign(Gtk.Align.CENTER)
        self._content.set_hexpand(True)
        self._scroller.set_child(self._content)
        self.append(self._scroller)

    def render(self, query: str) -> None:
        self._clear()
        q = query.strip()
        if not q:
            self._content.append(empty_state(
                _("Type to search"),
                _("Search across all AnduinOS documentation — articles, headings, code, tags, and commands."),
                icon_name="system-search-symbolic",
            ))
            return

        # Title row
        title = Gtk.Label(label=_("Search results for “{query}”").format(query=q))
        title.set_xalign(0)
        title.add_css_class("title-2")
        self._content.append(title)

        results = search_engine.search(q, limit=40)
        if not results:
            self._content.append(empty_state(
                _("No results found"),
                _("Try different keywords, or browse by category from the sidebar."),
                icon_name="system-search-symbolic",
            ))
            return

        count_label = Gtk.Label(label=_("{n} articles matched").format(n=len(results)))
        count_label.set_xalign(0)
        count_label.add_css_class("dim-label")
        count_label.add_css_class("caption")
        self._content.append(count_label)

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        for r in results:
            row = Adw.ActionRow()
            row.set_title(r.title)
            row.set_subtitle(r.summary)
            tags_label = " · ".join([r.display_category] + (r.matched_keywords[:3]))
            badges = Gtk.Label(label=tags_label)
            badges.add_css_class("dim-label")
            badges.add_css_class("caption")
            row.add_suffix(badges)
            arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
            arrow.add_css_class("dim-label")
            row.add_suffix(arrow)
            row.set_activatable(True)
            row.connect("activated", lambda _r, aid=r.article_id: self._navigate(aid))
            list_box.append(row)

        self._content.append(list_box)

        # Command matches
        cmds = search_engine.search_commands(q, limit=5)
        if cmds:
            cmd_title = Gtk.Label(label=_("Matching terminal commands"))
            cmd_title.set_xalign(0)
            cmd_title.add_css_class("title-3")
            cmd_title.set_margin_top(18)
            self._content.append(cmd_title)

            for c in cmds:
                row = Adw.ActionRow()
                row.set_title(GLib.markup_escape_text(c.command))
                # Use markup for monospace command
                title_lbl = Gtk.Label(label=f"<tt>{GLib.markup_escape_text(c.command)}</tt>")
                title_lbl.set_use_markup(True)
                title_lbl.set_xalign(0)
                title_lbl.add_css_class("monospace")
                # Strip subtitle (Adw.ActionRow only supports one title)
                sub = Gtk.Label(label=f"in {c.article_title}")
                sub.add_css_class("caption")
                sub.add_css_class("dim-label")
                row.add_suffix(sub)
                arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
                arrow.add_css_class("dim-label")
                row.add_suffix(arrow)
                row.set_activatable(True)
                row.connect("activated", lambda _r, aid=c.article_id: self._navigate(aid))
                list_box = Gtk.ListBox()
                list_box.add_css_class("boxed-list")
                list_box.append(row)
                self._content.append(list_box)

    def _clear(self) -> None:
        child = self._content.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._content.remove(child)
            child = nxt

    def _navigate(self, article_id: str) -> None:
        if self._on_navigate_article:
            self._on_navigate_article(article_id)
