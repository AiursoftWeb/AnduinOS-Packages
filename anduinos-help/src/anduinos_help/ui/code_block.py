"""Code block widget with copy button, language badge, and syntax styling."""
from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

from anduinos_help.ui.widgets import copy_to_clipboard


# Languages we know how to display. Anything else falls back to plain text.
KNOWN_LANGS = {
    "bash", "sh", "shell", "console", "shell-session", "zsh", "fish",
    "python", "py",
    "javascript", "js", "typescript", "ts",
    "json", "yaml", "yml", "toml", "ini", "conf",
    "html", "xml", "css",
    "c", "cpp", "csharp", "cs", "go", "rust", "rs", "java",
    "sql", "diff", "markdown", "md",
}


def _normalize_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    l = lang.lower().strip()
    if l in {"shell-session", "console"}:
        return "bash"
    if l in {"py"}:
        return "python"
    if l in {"js"}:
        return "javascript"
    if l in {"ts"}:
        return "typescript"
    if l in {"yml"}:
        return "yaml"
    if l in {"md"}:
        return "markdown"
    if l in {"cs"}:
        return "csharp"
    if l in {"rs"}:
        return "rust"
    return l if l in KNOWN_LANGS else None


class CodeBlock(Gtk.Box):
    """A code block with header (language + copy) and scrollable body."""

    def __init__(self, text: str, language: str | None = None, is_root: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._text = text
        self._language = _normalize_lang(language)
        self._is_root = is_root
        self.add_css_class("code-block")
        self.build_ui()

    def build_ui(self) -> None:
        # Header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("code-block-header")

        lang_label_text = self._language or "text"
        lang = Gtk.Label(label=lang_label_text)
        lang.add_css_class("dim-label")
        lang.add_css_class("code-block-lang")
        lang.set_xalign(0)
        lang.set_hexpand(True)

        if self._is_root:
            root_badge = Gtk.Label(label="root")
            root_badge.add_css_class("code-block-root-badge")
            header.append(root_badge)

        copy_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy to clipboard")
        copy_btn.set_can_focus(False)
        copy_btn.connect("clicked", self._on_copy)

        header.append(lang)
        header.append(copy_btn)
        self.append(header)

        # Body — scrolled horizontally, wraps vertically
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.set_min_content_height(0)
        scroller.add_css_class("code-block-scroller")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("code-block-body")

        label = Gtk.Label(label=self._text)
        label.set_xalign(0)
        label.set_yalign(0)
        label.set_halign(Gtk.Align.START)
        label.set_selectable(True)
        label.set_wrap(False)
        label.add_css_class("code-block-text")
        label.add_css_class("monospace")

        body.set_margin_start(14)
        body.set_margin_end(14)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.append(label)
        scroller.set_child(body)
        self.append(scroller)

    def _on_copy(self, _btn: Gtk.Button) -> None:
        copy_to_clipboard(self._text)
        _btn.set_icon_name("object-select-symbolic")
        GLib.timeout_add_seconds(2, lambda: (_btn.set_icon_name("edit-copy-symbolic"), False)[1])
