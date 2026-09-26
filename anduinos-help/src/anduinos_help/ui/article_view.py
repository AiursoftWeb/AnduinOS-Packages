"""Article renderer: walks the parser IR and emits GTK4 widgets."""
from __future__ import annotations

from typing import Any

from gi.repository import GLib, Gtk, Pango

from anduinos_help.docs import loader, parser
from anduinos_help.i18n import _
from anduinos_help.ui.code_block import CodeBlock
from anduinos_help.ui.widgets import make_callout, make_link_label
from anduinos_help.utils.links import (
    is_internal_link,
    is_safe_external,
    open_external,
    parse_internal_article,
)


class ArticleView(Gtk.Box):
    """A scrollable article view.

    Construct with an ``Article`` and call ``render()`` to populate.
    Pass ``on_navigate`` to receive internal article ids when the user
    clicks an in-article link.
    """

    def __init__(self, on_navigate: Any | None = None, on_external: Any | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_navigate = on_navigate
        self._on_external = on_external or open_external
        self._article: loader.Article | None = None
        self._blocks: list[dict] = []

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_propagate_natural_height(True)
        self._scroller.set_hexpand(True)
        self._scroller.set_vexpand(True)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._content.set_margin_top(34)
        self._content.set_margin_bottom(60)
        self._content.set_margin_start(56)
        self._content.set_margin_end(56)
        self._content.set_halign(Gtk.Align.CENTER)
        self._content.set_hexpand(True)
        self._scroller.set_child(self._content)
        self.append(self._scroller)

    @property
    def article(self) -> loader.Article | None:
        return self._article

    def render(self, article: loader.Article, blocks: list[dict] | None = None) -> None:
        self._article = article
        self._blocks = blocks if blocks is not None else loader.load_article_blocks(article)
        self._clear()
        self._render_header(article)
        for block in self._blocks:
            widget = self._render_block(block)
            if widget:
                self._content.append(widget)
        self._render_footer(article)

    def _clear(self) -> None:
        child = self._content.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._content.remove(child)
            child = nxt

    # ------------------------------------------------------------------
    # header
    # ------------------------------------------------------------------
    def _render_header(self, article: loader.Article) -> None:
        # Title
        title = Gtk.Label(label=article.title)
        title.set_xalign(0)
        title.set_wrap(True)
        title.add_css_class("article-title")
        title.add_css_class("title-1")
        self._content.append(title)

        # Summary
        if article.summary:
            summary = Gtk.Label(label=article.summary)
            summary.set_xalign(0)
            summary.set_wrap(True)
            summary.add_css_class("article-summary")
            summary.add_css_class("dim-label")
            summary.set_max_width_chars(120)
            self._content.append(summary)

        # Meta chips: version, status, last updated
        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        chips.set_margin_top(4)
        chips.append(self._make_chip(article.version, "version-chip"))
        if article.status == "legacy":
            chips.append(self._make_chip("Legacy", "status-legacy-chip"))
        elif article.status == "deprecated":
            chips.append(self._make_chip("Deprecated", "status-deprecated-chip"))
        if article.tags:
            chips.append(self._make_chip(f"Updated {article.updated}", "meta-chip"))
        self._content.append(chips)

        # Divider
        self._content.append(self._make_hr())

    def _make_chip(self, text: str, css_class: str) -> Gtk.Label:
        chip = Gtk.Label(label=text)
        chip.add_css_class("chip")
        chip.add_css_class(css_class)
        chip.set_xalign(0)
        return chip

    # ------------------------------------------------------------------
    # block dispatch
    # ------------------------------------------------------------------
    def _render_block(self, block: dict) -> Gtk.Widget | None:
        t = block["type"]
        if t == "heading":
            return self._render_heading(block)
        if t == "paragraph":
            return self._render_paragraph(block)
        if t == "code":
            return CodeBlock(block["text"], block.get("language"), block.get("is_root", False))
        if t == "list":
            return self._render_list(block)
        if t == "blockquote":
            return self._render_blockquote(block)
        if t == "table":
            return self._render_table(block)
        if t == "hr":
            return self._make_hr()
        if t == "callout":
            return self._render_callout(block)
        if t == "html_block":
            return None  # don't render raw HTML for safety
        return None

    def _render_heading(self, block: dict) -> Gtk.Label:
        level = block["level"]
        css = {1: "title-1", 2: "title-2", 3: "title-3", 4: "title-4", 5: "title-4", 6: "title-4"}[level]
        label = Gtk.Label()
        markup = self._render_spans_markup(block.get("spans", []))
        label.set_markup(markup)
        label.set_xalign(0)
        label.set_wrap(True)
        label.set_use_markup(True)
        label.add_css_class("article-heading")
        label.add_css_class(css)
        label.connect("activate-link", self._on_activate_link)
        if level == 1:
            label.set_margin_top(6)
        else:
            label.set_margin_top(18)
        return label

    def _render_paragraph(self, block: dict) -> Gtk.Label:
        label = Gtk.Label()
        markup = self._render_spans_markup(block.get("spans", []))
        label.set_markup(markup)
        label.set_xalign(0)
        label.set_wrap(True)
        label.set_use_markup(True)
        label.set_max_width_chars(120)
        label.add_css_class("article-paragraph")
        label.connect("activate-link", self._on_activate_link)
        return label

    def _on_activate_link(self, _label: Gtk.Label, uri: str) -> bool:
        """Intercept all <a href> clicks inside the article."""
        if is_internal_link(uri):
            aid = parse_internal_article(uri)
            if aid:
                self._navigate(aid)
                return True
            return False
        if is_safe_external(uri):
            self._on_external(uri)
            return True
        # Block anything else (e.g. javascript:)
        return True

    def _render_spans_markup(self, spans: list[dict]) -> str:
        out: list[str] = []
        for s in spans:
            st = s.get("type")
            text = s.get("text", "")
            esc = GLib.markup_escape_text(text)
            style = s.get("style") or set()
            url = s.get("url")
            if "code" in style or st == "code":
                esc = f"<tt>{esc}</tt>"
            if "strong" in style:
                esc = f"<b>{esc}</b>"
            if "em" in style:
                esc = f"<i>{esc}</i>"
            if "strike" in style:
                esc = f"<s>{esc}</s>"
            if url:
                if is_internal_link(url):
                    esc = f'<a href="{url}">{esc}</a>'
                elif is_safe_external(url):
                    esc = f'<a href="{url}">{esc}</a>'
            if st == "image":
                alt = s.get("alt") or ""
                out.append(GLib.markup_escape_text(alt))
                continue
            if st == "br":
                out.append("\n")
                continue
            out.append(esc)
        return "".join(out)

    def _render_list(self, block: dict) -> Gtk.Box:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(8)
        for i, item_blocks in enumerate(block["items"]):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_top(2)
            row.set_margin_bottom(2)
            if block["ordered"]:
                marker = Gtk.Label(label=f"{i+1}.")
            else:
                marker = Gtk.Label(label="•")
            marker.set_xalign(0)
            marker.set_valign(Gtk.Align.START)
            marker.add_css_class("list-marker")
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            content.set_hexpand(True)
            for blk in item_blocks:
                w = self._render_block(blk)
                if w:
                    content.append(w)
            row.append(marker)
            row.append(content)
            outer.append(row)
        return outer

    def _render_blockquote(self, block: dict) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("blockquote")
        box.set_margin_start(14)
        box.set_margin_end(8)
        for blk in block.get("blocks", []):
            w = self._render_block(blk)
            if w:
                box.append(w)
        return box

    def _render_callout(self, block: dict) -> Gtk.Widget:
        return make_callout(
            block["kind"],
            block.get("blocks", []),
            title=block.get("title"),
            render_block=self._render_block,
        )

    def _render_table(self, block: dict) -> Gtk.Box:
        # Build a grid-like display using Gtk.Grid
        grid = Gtk.Grid()
        grid.set_column_spacing(14)
        grid.set_row_spacing(6)
        grid.set_margin_top(8)
        grid.set_margin_bottom(8)
        grid.add_css_class("article-table")
        headers = block["headers"]
        rows = block["rows"]
        for c, h in enumerate(headers):
            lbl = Gtk.Label(label=h)
            lbl.set_xalign(0)
            lbl.add_css_class("table-header")
            grid.attach(lbl, c, 0, 1, 1)
        for r, row in enumerate(rows, start=1):
            for c, cell in enumerate(row):
                lbl = Gtk.Label(label=cell)
                lbl.set_xalign(0)
                lbl.set_wrap(True)
                lbl.set_max_width_chars(50)
                if c < len(headers):
                    grid.attach(lbl, c, r, 1, 1)
                else:
                    grid.attach(lbl, c, r, 1, 1)
        # Wrap in a horizontal scroller
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(grid)
        return scroller

    def _make_hr(self) -> Gtk.Widget:
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.add_css_class("article-separator")
        return sep

    # ------------------------------------------------------------------
    # footer
    # ------------------------------------------------------------------
    def _render_footer(self, article: loader.Article) -> None:
        self._content.append(self._make_hr())

        # Related articles
        related = loader.load_catalog().related(article.id, limit=5)
        if related:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            title = Gtk.Label(label=_("Related articles"))
            title.set_xalign(0)
            title.add_css_class("title-4")
            title.set_margin_bottom(4)
            box.append(title)
            for r in related:
                # Use a custom label child so the button text is left-justified
                # rather than centered (Gtk.Button centers its label by default).
                btn_label = Gtk.Label(label=f"›  {r.title}")
                btn_label.set_xalign(0)
                btn_label.set_hexpand(True)
                btn = Gtk.Button()
                btn.set_child(btn_label)
                btn.add_css_class("flat")
                btn.set_halign(Gtk.Align.START)
                btn.set_hexpand(True)
                btn.connect("clicked", lambda _b, aid=r.id: self._navigate(aid))
                box.append(btn)
            self._content.append(box)

        # Source attribution
        src_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        src_box.set_margin_top(8)
        src_title = Gtk.Label(label=_("Source"))
        src_title.set_xalign(0)
        src_title.add_css_class("title-4")
        src_box.append(src_title)

        src_label = Gtk.Label(label=_("Official AnduinOS Documentation"))
        src_label.set_xalign(0)
        src_label.add_css_class("dim-label")
        src_box.append(src_label)

        if article.source_url:
            link = Gtk.Label()
            link.set_markup(
                f'<a href="{article.source_url}">{_("Open source ↗")}</a>'
            )
            link.set_xalign(0)
            link.connect("activate-link", lambda _l, _u: self._on_external(article.source_url) or True)
            src_box.append(link)

        if article.source_repo_url:
            repo_link = Gtk.Label()
            repo_link.set_markup(
                f'<a href="{article.source_repo_url}">{_("View source on GitHub ↗")}</a>'
            )
            repo_link.set_xalign(0)
            repo_link.connect("activate-link", lambda _l, _u: self._on_external(article.source_repo_url) or True)
            src_box.append(repo_link)

        # License
        lic = Gtk.Label(label=f"Licensed under {article.license}")
        lic.set_xalign(0)
        lic.add_css_class("dim-label")
        lic.add_css_class("caption")
        src_box.append(lic)

        # Footer info
        info = Gtk.Label(label=f"Applies to: AnduinOS {article.version}  ·  Last updated: {article.updated}")
        info.set_xalign(0)
        info.add_css_class("dim-label")
        info.add_css_class("caption")
        info.set_margin_top(4)
        src_box.append(info)

        self._content.append(src_box)

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def _navigate(self, article_id: str) -> None:
        if self._on_navigate:
            self._on_navigate(article_id)

    def connect_link_handler(self) -> None:
        """Connect the activate-link signal to handle internal navigation."""

        # activate-link on each label is connected at creation time.
        # Nothing more to do here — kept for forward-compat.
        return
