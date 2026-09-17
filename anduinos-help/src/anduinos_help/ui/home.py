"""Home page: a polished Help Center landing."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, Gtk

from anduinos_help.docs import loader
from anduinos_help.i18n import _
from anduinos_help.system import version
from anduinos_help.utils.logging import get_logger

_log = get_logger("ui.home")


class HomePage(Gtk.Box):
    """The Help Center home page."""

    def __init__(self, on_navigate: Any | None = None, on_search: Any | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._on_navigate = on_navigate
        self._on_search = on_search

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_hexpand(True)
        self._scroller.set_vexpand(True)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self._content.set_margin_top(54)
        self._content.set_margin_bottom(60)
        self._content.set_margin_start(56)
        self._content.set_margin_end(56)
        self._content.set_halign(Gtk.Align.CENTER)
        self._content.set_halign(Gtk.Align.CENTER)
        self._content.set_hexpand(True)
        self._scroller.set_child(self._content)
        self.append(self._scroller)

        self._build()

    def _build(self) -> None:
        # Hero
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero.set_halign(Gtk.Align.CENTER)
        hero.set_margin_bottom(8)

        welcome = Gtk.Label(label=_("Welcome to AnduinOS"))
        welcome.add_css_class("title-1")
        welcome.set_halign(Gtk.Align.CENTER)
        hero.append(welcome)

        tagline = Gtk.Label(label=_("How can we help?"))
        tagline.add_css_class("title-3")
        tagline.add_css_class("dim-label")
        tagline.set_halign(Gtk.Align.CENTER)
        hero.append(tagline)

        # Big search entry
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search documentation, commands, troubleshooting…"))
        self._search.set_halign(Gtk.Align.CENTER)
        self._search.set_size_request(560, -1)
        self._search.set_max_width_chars(60)
        self._search.connect("activate", self._on_search_activate)
        self._search.connect("search-changed", self._on_search_changed)
        hero.append(self._search)
        hero.set_margin_top(8)

        self._content.append(hero)

        # Version info card
        self._content.append(self._build_version_card())

        # Popular topics
        self._content.append(self._build_popular_topics())

        # Recommended guides
        self._content.append(self._build_recommended_guides())

        # Recent documentation
        self._content.append(self._build_recent_articles())

        # Footer links
        self._content.append(self._build_footer_links())

    def focus_search(self) -> None:
        self._search.grab_focus()

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------
    def _build_version_card(self) -> Gtk.Widget:
        sv = version.detect()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("card")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(14)
        inner.set_margin_bottom(14)
        inner.set_margin_start(18)
        inner.set_margin_end(18)

        title = Gtk.Label(label=_("Your AnduinOS"))
        title.set_xalign(0)
        title.add_css_class("title-4")
        inner.append(title)

        if sv.is_anduinos:
            ver = f"AnduinOS {sv.anduinos_version or ''}".strip()
            codename = f" ({sv.anduinos_codename})" if sv.anduinos_codename else ""
            info = f"Running {ver}{codename}  ·  Kernel {sv.kernel or 'unknown'}"
        else:
            info = f"Running {sv.short()}  ·  Kernel {sv.kernel or 'unknown'}  ·  Documentation applies to AnduinOS 2.x"
        label = Gtk.Label(label=info)
        label.set_xalign(0)
        label.set_wrap(True)
        label.add_css_class("dim-label")
        inner.append(label)

        offline = Gtk.Label(label=_("Documentation is bundled for offline use."))
        offline.set_xalign(0)
        offline.add_css_class("caption")
        offline.add_css_class("dim-label")
        inner.append(offline)

        card.append(inner)
        return card

    def _build_popular_topics(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label=_("Popular topics"))
        title.set_xalign(0)
        title.add_css_class("title-2")
        section.append(title)

        topics = [
            (_("Getting Started"), "getting-started", "go-home-symbolic"),
            (_("Installation"), "install", "system-software-install-symbolic"),
            (_("Desktop"), "desktop", "computer-symbolic"),
            (_("Applications"), "applications", "applications-system-symbolic"),
            (_("Troubleshooting"), "troubleshooting", "tools-check-spelling-symbolic"),
            (_("Developers"), "developers", "applications-development-symbolic"),
        ]

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(3)

        for label, category, icon in topics:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("card")
            card.set_margin_top(2)
            card.set_margin_bottom(2)
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            inner.set_margin_top(14)
            inner.set_margin_bottom(14)
            inner.set_margin_start(16)
            inner.set_margin_end(16)

            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(22)
            inner.append(img)

            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text_box.set_hexpand(True)
            lbl = Gtk.Label(label=label)
            lbl.set_xalign(0)
            lbl.add_css_class("title-4")
            text_box.append(lbl)
            chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
            chev.set_pixel_size(14)
            chev.add_css_class("dim-label")
            inner.append(text_box)
            inner.append(chev)

            card.append(inner)
            click = Gtk.GestureClick.new()
            click.connect("released", lambda _g, _n, _x, _y, c=category: self._navigate_category(c))
            card.add_controller(click)

            flow.append(card)

        section.append(flow)
        return section

    def _build_recommended_guides(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label=_("Recommended guides"))
        title.set_xalign(0)
        title.add_css_class("title-2")
        section.append(title)

        cat = loader.load_catalog()
        recommended_ids = [
            "getting-started/readme",
            "install/download-anduinos",
            "install/update-your-system",
            "install/installing-applications",
            "developers/apkg-introduction",
            "releases/2-0-3",
        ]
        guides = []
        for aid in recommended_ids:
            a = cat.get(aid)
            if a:
                guides.append(a)
        if not guides:
            # Fallback: take first 6 articles from install category
            for a in cat.by_category("install")[:6]:
                guides.append(a)
        if not guides:
            return section

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(2)

        for art in guides[:6]:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("card")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            inner.set_margin_top(14)
            inner.set_margin_bottom(14)
            inner.set_margin_start(16)
            inner.set_margin_end(16)

            t = Gtk.Label(label=art.title)
            t.set_xalign(0)
            t.set_wrap(True)
            t.add_css_class("title-4")
            inner.append(t)

            cat_label = Gtk.Label(label=art.display_category)
            cat_label.set_xalign(0)
            cat_label.add_css_class("caption")
            cat_label.add_css_class("dim-label")
            inner.append(cat_label)

            if art.summary:
                s = Gtk.Label(label=art.summary)
                s.set_xalign(0)
                s.set_wrap(True)
                s.set_max_width_chars(80)
                s.add_css_class("dim-label")
                inner.append(s)

            card.append(inner)
            click = Gtk.GestureClick.new()
            click.connect("released", lambda _g, _n, _x, _y, aid=art.id: self._navigate_article(aid))
            card.add_controller(click)

            flow.append(card)

        section.append(flow)
        return section

    def _build_recent_articles(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label=_("Recent documentation"))
        title.set_xalign(0)
        title.add_css_class("title-2")
        section.append(title)

        cat = loader.load_catalog()
        recent = sorted(
            cat.articles.values(),
            key=lambda a: a.updated or "",
            reverse=True,
        )[:8]

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        for art in recent:
            row = Adw.ActionRow()
            row.set_title(art.title)
            row.set_subtitle(f"{art.display_category}  ·  Updated {art.updated}")
            row.set_activatable(True)
            arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
            arrow.add_css_class("dim-label")
            row.add_suffix(arrow)
            row.connect("activated", lambda _r, aid=art.id: self._navigate_article(aid))
            list_box.append(row)

        section.append(list_box)
        return section

    def _build_footer_links(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label=_("Resources"))
        title.set_xalign(0)
        title.add_css_class("title-2")
        section.append(title)

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        resources = [
            (_("Official AnduinOS Documentation"), "https://docs.anduinos.com/", "text-html-symbolic"),
            (_("AnduinOS Documentation Source (GitHub)"), "https://github.com/AiursoftWeb/AnduinOS-Docs", "system-software-install-symbolic"),
            (_("Report a Problem"), "https://github.com/AiursoftWeb/AnduinOS-2/issues", "dialog-warning-symbolic"),
            (_("AnduinOS Homepage"), "https://www.anduinos.com/", "go-home-symbolic"),
        ]

        from anduinos_help.utils.links import open_external
        for label, url, icon in resources:
            row = Adw.ActionRow()
            row.set_title(label)
            row.set_subtitle(url)
            row.set_activatable(True)
            img = Gtk.Image.new_from_icon_name(icon)
            row.add_prefix(img)
            arrow = Gtk.Image.new_from_icon_name("external-link-symbolic")
            arrow.add_css_class("dim-label")
            row.add_suffix(arrow)
            row.connect("activated", lambda _r, u=url: open_external(u))
            list_box.append(row)

        section.append(list_box)
        return section

    # ------------------------------------------------------------------
    # interactions
    # ------------------------------------------------------------------
    def _navigate_category(self, category: str) -> None:
        if self._on_navigate:
            self._on_navigate("category", category)

    def _navigate_article(self, article_id: str) -> None:
        if self._on_navigate:
            self._on_navigate("article", article_id)

    def _on_search_activate(self, _entry) -> None:
        text = self._search.get_text().strip()
        if text and self._on_search:
            self._on_search(text)

    def _on_search_changed(self, _entry) -> None:
        # Live search is invoked by the main window's header search entry;
        # the home page entry also forwards to the global search.
        pass
