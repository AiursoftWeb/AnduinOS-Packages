"""Main application window."""
from __future__ import annotations

from typing import Any

from gi.repository import Adw, GLib, Gtk

from anduinos_help.docs import loader
from anduinos_help.i18n import _
from anduinos_help.ui.article_view import ArticleView
from anduinos_help.ui.category import CategoryPage
from anduinos_help.ui.home import HomePage
from anduinos_help.ui.search import SearchPage
from anduinos_help.ui.sidebar import Sidebar
from anduinos_help.ui.widgets import make_breadcrumbs
from anduinos_help.utils.logging import get_logger

_log = get_logger("ui.window")


class HelpWindow(Adw.ApplicationWindow):
    """The main Help Center window."""

    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app)
        self.set_title(_("AnduinOS Help"))
        self.set_default_size(1200, 780)
        self.set_icon_name("com.anduinos.Help")

        # Track current view state
        self._history: list[tuple[str, str | None]] = [("home", None)]

        # Root container: toolbar with header + content
        self._toolbar = Adw.ToolbarView()
        self.set_content(self._toolbar)

        # Header bar with global search
        self._header = Adw.HeaderBar()
        self._header.set_show_end_title_buttons(True)
        self._toolbar.add_top_bar(self._header)

        # Global search
        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text(_("Search documentation…"))
        self._search.set_hexpand(True)
        self._search.set_max_width_chars(48)
        self._search.connect("search-changed", self._on_search_changed)
        self._search.connect("stop-search", self._on_search_stop)
        self._header.set_title_widget(self._search)

        # Menu button
        menu = self._build_menu()
        menu_btn = Gtk.MenuButton.new()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        menu_btn.set_tooltip_text(_("Menu"))
        self._header.pack_end(menu_btn)

        # Back button
        self._back_btn = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self._back_btn.set_tooltip_text(_("Back"))
        self._back_btn.connect("clicked", self._on_back_clicked)
        self._header.pack_start(self._back_btn)

        # Split view: sidebar + content
        self._split = Adw.NavigationSplitView()
        self._split.set_min_sidebar_width(240)
        self._split.set_max_sidebar_width(320)
        self._split.set_sidebar_width_fraction(0.22)

        self._sidebar = Sidebar(
            on_select_category=self._navigate_to_category,
            on_home=self._navigate_home,
            on_special=self._on_sidebar_special,
        )
        sidebar_page = Adw.NavigationPage.new(self._sidebar, "Topics")
        self._split.set_sidebar(sidebar_page)

        # Content area: a stack of pages (home, category, article, search)
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(180)
        content_page = Adw.NavigationPage.new(self._stack, "Content")
        self._split.set_content(content_page)

        self._toolbar.set_content(self._split)

        # Build pages lazily
        self._home_page = HomePage(
            on_navigate=self._on_home_navigate,
            on_search=self._on_home_search,
        )
        self._category_page = CategoryPage(on_navigate_article=self._navigate_to_article)
        self._article_view = ArticleView(on_navigate=self._navigate_to_article)
        self._search_page = SearchPage(on_navigate_article=self._navigate_to_article)

        self._stack.add_named(self._home_page, "home")
        self._stack.add_named(self._category_page, "category")
        self._stack.add_named(self._article_view, "article")
        self._stack.add_named(self._search_page, "search")

        self._stack.set_visible_child_name("home")
        self._update_back_button()

    def _build_menu(self) -> Gio.Menu:
        from gi.repository import Gio
        menu = Gio.Menu.new()
        menu.append(_("Home"), "win.go-home")
        menu.append(_("Check for documentation updates"), "win.check-updates")
        menu.append(_("System Diagnostics"), "win.diagnostics")
        menu.append(_("Report a Problem"), "win.report-problem")
        menu.append(_("Glossary"), "win.glossary")
        menu.append(_("Toggle Fullscreen"), "win.fullscreen")
        menu.append(_("Keyboard Shortcuts"), "win.shortcuts")
        menu.append(_("About AnduinOS Help"), "app.about")
        return menu

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def _on_home_navigate(self, kind: str, key: str) -> None:
        if kind == "category":
            self._navigate_to_category(key)
        elif kind == "article":
            self._navigate_to_article(key)

    def _on_home_search(self, query: str) -> None:
        self._search.set_text(query)
        self._show_search(query)

    def _navigate_home(self) -> None:
        self._push_history("home", None)
        self._stack.set_visible_child_name("home")
        self._sidebar.select_home()
        self._search.set_text("")
        self._update_back_button()

    def _navigate_to_category(self, category: str) -> None:
        self._push_history("category", category)
        self._category_page.render(category)
        self._stack.set_visible_child_name("category")
        self._update_back_button()

    def _navigate_to_article(self, article_id: str) -> None:
        cat = loader.load_catalog()
        art = cat.get(article_id)
        if not art:
            _log.warning("Article not found: %s", article_id)
            return
        self._push_history("article", article_id)
        blocks = loader.load_article_blocks(art)
        self._article_view.render(art, blocks)
        self._stack.set_visible_child_name("article")
        # Scroll article to top
        scroller = self._article_view._scroller  # type: ignore[attr-defined]
        adj = scroller.get_vadjustment()
        adj.set_value(0)
        self._update_back_button()

    def _push_history(self, kind: str, key: str | None) -> None:
        # Truncate forward history
        self._history.append((kind, key))
        if len(self._history) > 32:
            self._history = self._history[-32:]

    def _on_back_clicked(self, _btn) -> None:
        if len(self._history) <= 1:
            return
        self._history.pop()
        kind, key = self._history[-1]
        if kind == "home":
            self._stack.set_visible_child_name("home")
            self._sidebar.select_home()
        elif kind == "category":
            self._category_page.render(key)
            self._stack.set_visible_child_name("category")
        elif kind == "article":
            cat = loader.load_catalog()
            art = cat.get(key)
            if art:
                self._article_view.render(art, loader.load_article_blocks(art))
                self._stack.set_visible_child_name("article")
        self._update_back_button()

    def _update_back_button(self) -> None:
        self._back_btn.set_sensitive(len(self._history) > 1)

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text().strip()
        if not text:
            # If we were viewing search results, go back to the previous view
            if self._stack.get_visible_child_name() == "search":
                kind, key = self._history[-1]
                if kind == "category":
                    self._category_page.render(key)
                    self._stack.set_visible_child_name("category")
                else:
                    self._stack.set_visible_child_name("home")
                    self._sidebar.select_home()
            return
        self._show_search(text)

    def _show_search(self, query: str) -> None:
        self._search_page.render(query)
        self._stack.set_visible_child_name("search")

    def _on_search_stop(self, _entry) -> None:
        self._search.set_text("")
        # Go back
        if self._stack.get_visible_child_name() == "search":
            kind, key = self._history[-1]
            if kind == "category":
                self._category_page.render(key)
                self._stack.set_visible_child_name("category")
            else:
                self._stack.set_visible_child_name("home")
                self._sidebar.select_home()

    # ------------------------------------------------------------------
    # public actions
    # ------------------------------------------------------------------
    def go_home(self) -> None:
        self._navigate_home()

    def _on_sidebar_special(self, key: str) -> None:
        """Handle sidebar entries that open dialogs (not category pages)."""
        # Re-select the previous row so the sidebar's selection state
        # reflects the current page rather than the dialog that just opened.
        GLib.idle_add(self._restore_sidebar_selection)
        if key == "diagnostics":
            self.show_diagnostics()
        elif key == "glossary":
            self.show_glossary()
        elif key == "about":
            from anduinos_help.ui.dialogs import show_about
            show_about(self)
        elif key == "report-problem":
            self.report_problem()
        elif key == "check-updates":
            self.check_updates()

    def _restore_sidebar_selection(self) -> bool:
        """Re-select the sidebar row matching the current page."""
        kind, _ = self._history[-1] if self._history else ("home", None)
        # Map current view → sidebar key
        target_key: str | None = None
        if kind == "home":
            target_key = "home"
        elif kind == "category":
            # Look up the category of the current view
            # (We stored the category as the second history element.)
            target_key = self._history[-1][1] if len(self._history) > 0 else None
        if target_key:
            # Find the row with this key
            i = 0
            while True:
                row = self._sidebar._list.get_row_at_index(i)  # type: ignore[attr-defined]
                if row is None:
                    break
                if getattr(row, "row_key", None) == target_key:
                    self._sidebar._list.select_row(row)  # type: ignore[attr-defined]
                    break
                i += 1
        return False

    def check_updates(self) -> None:
        from anduinos_help.ui.dialogs import show_update_docs
        show_update_docs(self)

    def show_diagnostics(self) -> None:
        from anduinos_help.ui.dialogs import show_diagnostics
        show_diagnostics(self)

    def report_problem(self) -> None:
        from anduinos_help.ui.dialogs import show_report_problem
        show_report_problem(self)

    def show_glossary(self) -> None:
        from anduinos_help.ui.dialogs import show_glossary
        show_glossary(self)

    def toggle_fullscreen(self) -> None:
        """Toggle fullscreen mode (F11)."""
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def show_shortcuts(self) -> None:
        """Show the keyboard shortcuts window."""
        from anduinos_help.ui.dialogs import show_shortcuts
        show_shortcuts(self)
