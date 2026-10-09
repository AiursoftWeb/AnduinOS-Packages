"""Modal dialogs: About, Diagnostics, Refresh, Report Problem, Glossary."""
from __future__ import annotations

from gi.repository import Adw, Gdk, GLib, Gtk

from anduinos_help.docs import loader, sync
from anduinos_help.docs import search as search_engine
from anduinos_help.i18n import _
from anduinos_help.system import diagnostics
from anduinos_help.utils.logging import get_logger
from anduinos_help.utils.links import open_external

_log = get_logger("ui.dialogs")

APP_ID = "com.anduinos.Help"
APP_NAME = "AnduinOS Help"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = (
    "Native documentation browser for AnduinOS. Periodically clones the "
    "official AnduinOS-Docs git repository and indexes its Markdown into a "
    "local SQLite database, so search and article rendering work fully "
    "offline. Source: https://github.com/AiursoftWeb/AnduinOS-Docs"
)
APP_WEBSITE = "https://docs.anduinos.com/"
# User-facing "Report a Problem" — for AnduinOS OS issues that users encounter.
APP_ISSUES = "https://github.com/AiursoftWeb/AnduinOS-2/issues"
# Self-issue URL — for bugs in the Help application itself (used in About dialog).
APP_SELF_ISSUES = "https://github.com/AiursoftWeb/AnduinOS-Packages/issues"
APP_DOCS_REPO = "https://github.com/AiursoftWeb/AnduinOS-Docs"
APP_LICENSE = "GPL-3.0"
APP_LICENSE_URL = "https://github.com/AiursoftWeb/AnduinOS-Docs/blob/master/LICENSE"
APP_DEVELOPERS = ["AnduinOS Contributors"]
APP_ARTISTS = ["AnduinOS Design Team"]
APP_COPYRIGHT = "© 2024–2026 AnduinOS Contributors"


def show_about(parent: Gtk.Widget) -> None:
    """Show the standard About dialog."""
    about = Adw.AboutWindow(transient_for=parent)
    about.set_application_name(APP_NAME)
    about.set_application_icon("help-browser")
    about.set_version(APP_VERSION)
    about.set_developer_name("AnduinOS Team")
    about.set_developers(APP_DEVELOPERS)
    about.set_artists(APP_ARTISTS)
    about.set_copyright(APP_COPYRIGHT)
    about.set_license_type(Gtk.License.GPL_3_0)
    about.set_website(APP_WEBSITE)
    # libadwaita 1.7 removed set_website_label — the URL is shown as-is.
    about.set_issue_url(APP_SELF_ISSUES)
    about.set_comments(APP_DESCRIPTION)
    about.set_translator_credits("translator-credits")
    about.present()


def show_diagnostics(parent: Gtk.Widget) -> None:
    """Show a system diagnostics dialog with a Copy button."""
    dialog = Adw.Dialog()
    dialog.set_title(_("System Diagnostics"))
    dialog.set_content_width(640)
    dialog.set_content_height(560)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    toolbar.add_top_bar(header)

    copy_btn = Gtk.Button.new_with_label(_("Copy"))
    copy_btn.add_css_class("suggested-action")
    header.pack_end(copy_btn)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.set_margin_start(12)
    box.set_margin_end(12)

    textview = Gtk.TextView()
    textview.set_editable(False)
    textview.set_monospace(True)
    textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    textview.add_css_class("diagnostics-text")
    buffer = textview.get_buffer()

    scroll = Gtk.ScrolledWindow()
    scroll.set_vexpand(True)
    scroll.set_child(textview)
    box.append(scroll)
    toolbar.set_content(box)
    dialog.set_child(toolbar)

    copy_btn.connect("clicked", lambda _b: _copy_diagnostics(buffer))

    # Collect diagnostics in a thread to avoid blocking the UI
    def collect() -> bool:
        report = diagnostics.collect()
        text = report.to_markdown()
        GLib.idle_add(lambda: buffer.set_text(text) and False)
        return False

    import threading
    threading.Thread(target=collect, daemon=True).start()
    dialog.present(parent)


def _copy_diagnostics(buffer: Gtk.TextBuffer) -> None:
    start = buffer.get_start_iter()
    end = buffer.get_end_iter()
    text = buffer.get_text(start, end, True)
    display = Gdk.Display.get_default()
    if display:
        clipboard = display.get_clipboard()
        clipboard.set(text)


def show_report_problem(parent: Gtk.Widget) -> None:
    """Show the Report a Problem dialog with links and pre-filled template."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Report a Problem"))
    dialog.set_content_width(560)
    dialog.set_content_height(560)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    toolbar.add_top_bar(header)

    open_btn = Gtk.Button.new_with_label(_("Open Issue Tracker"))
    open_btn.add_css_class("suggested-action")
    header.pack_end(open_btn)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(16)
    box.set_margin_bottom(16)
    box.set_margin_start(20)
    box.set_margin_end(20)

    intro = Gtk.Label(label=(
        "If something is not working as expected in AnduinOS, you can file a "
        "report on the official issue tracker. The template below will help "
        "you include the most useful information."
    ))
    intro.set_wrap(True)
    intro.set_xalign(0)
    intro.set_max_width_chars(70)
    box.append(intro)

    # Build a copyable diagnostic template
    sv = diagnostics.collect()
    template = (
        "## Bug report\n\n"
        "**Summary:** <one-sentence description>\n\n"
        "**Steps to reproduce:**\n"
        "1. \n2. \n3. \n\n"
        "**Expected behaviour:**\n\n"
        "**Actual behaviour:**\n\n"
        "## System information\n"
        + sv.to_markdown()
    )

    textview = Gtk.TextView()
    textview.set_editable(True)
    textview.set_monospace(True)
    textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    buffer = textview.get_buffer()
    buffer.set_text(template)
    scroll = Gtk.ScrolledWindow()
    scroll.set_vexpand(True)
    scroll.set_child(textview)
    box.append(scroll)

    copy_btn = Gtk.Button.new_with_label(_("Copy to clipboard"))
    copy_btn.connect("clicked", lambda _b: _copy_diagnostics(buffer))
    box.append(copy_btn)

    toolbar.set_content(box)
    dialog.set_child(toolbar)

    open_btn.connect("clicked", lambda _b: open_external(APP_ISSUES))
    dialog.present(parent)


def show_refresh_status(parent: Gtk.Widget, on_synced=None) -> None:
    """Show the documentation refresh dialog.

    Triggers a background sync (git pull + re-index Markdown into SQLite)
    and shows progress + the last-sync timestamp.
    """
    dialog = Adw.Dialog()
    dialog.set_title(_("Refresh Documentation"))
    dialog.set_content_width(520)
    dialog.set_content_height(360)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    toolbar.add_top_bar(header)

    refresh_btn = Gtk.Button.new_with_label(_("Refresh now"))
    refresh_btn.add_css_class("suggested-action")
    header.pack_end(refresh_btn)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(20)
    box.set_margin_bottom(20)
    box.set_margin_start(24)
    box.set_margin_end(24)

    title = Gtk.Label(label=_("Documentation Sync"))
    title.set_xalign(0)
    title.add_css_class("title-2")
    box.append(title)

    info_text = (
        _("AnduinOS Help clones the official AnduinOS-Docs git repository "
          "and indexes its Markdown into a local SQLite database. Search "
          "and article rendering work fully offline after sync.\n\n"
          "Source: https://github.com/AiursoftWeb/AnduinOS-Docs")
    )
    info = Gtk.Label(label=info_text)
    info.set_wrap(True)
    info.set_xalign(0)
    info.add_css_class("dim-label")
    box.append(info)

    status_label = Gtk.Label()
    status_label.set_wrap(True)
    status_label.set_xalign(0)
    box.append(status_label)

    progress = Gtk.ProgressBar()
    progress.set_visible(False)
    box.append(progress)

    toast_overlay = Adw.ToastOverlay()
    toast_overlay.set_child(box)
    toolbar.set_content(toast_overlay)
    dialog.set_child(toolbar)

    def _update_status():
        info_dict = sync.last_sync_info()
        if sync.is_syncing():
            status_label.set_label(_("Syncing…"))
            progress.set_visible(True)
            progress.pulse()
            refresh_btn.set_sensitive(False)
        elif info_dict:
            n = info_dict.get("documents_total", 0)
            t = info_dict.get("synced_at", "")[:16].replace("T", " ")
            status_label.set_label(
                _("Last synced: {time}\nDocuments indexed: {count}").format(time=t, count=n)
            )
            progress.set_visible(False)
            refresh_btn.set_sensitive(True)
        else:
            status_label.set_label(_("Never synced — click Refresh now to download the documentation."))
            progress.set_visible(False)
            refresh_btn.set_sensitive(True)
        return False  # don't repeat

    def _refresh_clicked(_btn):
        progress.set_visible(True)
        progress.pulse()
        refresh_btn.set_sensitive(False)
        status_label.set_label(_("Syncing… cloning docs repo and indexing Markdown."))

        def worker():
            result = sync.sync_blocking()
            GLib.idle_add(lambda: _on_done(result) and False)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _on_done(result):
        progress.set_visible(False)
        refresh_btn.set_sensitive(True)
        if result.success:
            # Reload the in-memory catalog so the UI sees the new docs.
            loader.load_catalog(force=True)
            toast_overlay.add_toast(Adw.Toast.new(
                _("Synced {n} documents.").format(n=result.documents_indexed)
            ))
            if on_synced:
                on_synced()
        else:
            err = result.error or "unknown error"
            toast_overlay.add_toast(Adw.Toast.new(_("Sync failed: {err}").format(err=err)))
        _update_status()
        return False

    refresh_btn.connect("clicked", _refresh_clicked)
    dialog.present(parent)

    # Show initial status
    GLib.idle_add(_update_status)


def show_glossary(parent: Gtk.Widget, on_navigate_article=None) -> None:
    """Show a searchable glossary dialog."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Glossary"))
    dialog.set_content_width(640)
    dialog.set_content_height(560)

    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    toolbar.add_top_bar(header)

    search_entry = Gtk.SearchEntry()
    search_entry.set_placeholder_text(_("Filter terms…"))
    header.set_title_widget(search_entry)

    scroller = Gtk.ScrolledWindow()
    scroller.set_vexpand(True)

    list_box = Gtk.ListBox()
    list_box.add_css_class("boxed-list")
    list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    list_box.set_filter_func(_glossary_filter(search_entry))

    for term in sorted(GLOSSARY, key=lambda t: t.lower()):
        row = Adw.ActionRow()
        row.set_title(term)
        row.set_subtitle(GLOSSARY[term])
        list_box.append(row)
    scroller.set_child(list_box)
    toolbar.set_content(scroller)
    dialog.set_child(toolbar)

    search_entry.connect("search-changed", lambda _e: list_box.invalidate_filter())
    dialog.present(parent)


def _glossary_filter(entry: Gtk.SearchEntry):
    def filter_fn(row: Gtk.ListBoxRow) -> bool:
        text = entry.get_text().strip().lower()
        if not text:
            return True
        title = row.get_title() if hasattr(row, "get_title") else ""
        subtitle = row.get_subtitle() if hasattr(row, "get_subtitle") else ""
        return text in (title or "").lower() or text in (subtitle or "").lower()
    return filter_fn


GLOSSARY: dict[str, str] = {
    "APT": "Advanced Package Tool — Debian/Ubuntu's package manager, used to install, update, and remove .deb packages.",
    "Apkg": "AnduinOS's package build and publishing tool that wraps Debian packaging for AnduinOS-specific workflows.",
    "AppImage": "A portable Linux application format that runs without installation; a single executable file containing the application and its dependencies.",
    "Bootloader": "The first software that runs when a computer starts; GRUB is the default bootloader on AnduinOS.",
    "Debian package": "A .deb file containing software in Debian's package format, installable via apt or dpkg.",
    "debootstrap": "A Debian tool that installs a minimal Debian-compatible system into a directory; used to build AnduinOS images.",
    "Flatpak": "A Linux application distribution framework that runs apps in sandboxed environments with bundled dependencies.",
    "GNOME": "The default desktop environment on AnduinOS, providing the top bar, overview, settings, and core applications.",
    "GTK": "The GIMP Toolkit — the widget toolkit used by GNOME and AnduinOS native applications. GTK 4 is the current version.",
    "Libadwaita": "GNOME's library of high-level GTK 4 widgets that implement the GNOME HIG; used by AnduinOS native apps.",
    "Linux kernel": "The core of the operating system that manages hardware, processes, and memory. AnduinOS ships a recent LTS kernel.",
    "LUKS": "Linux Unified Key Setup — the standard for full-disk encryption on Linux.",
    "Repository": "A storage location for packages; apt repositories are configured in /etc/apt/sources.list and /etc/apt/sources.list.d/.",
    "Secure Boot": "A UEFI feature that ensures only signed bootloaders and kernels can run during boot; AnduinOS supports it.",
    "Snap": "Canonical's package format that bundles apps with their dependencies; installable on AnduinOS via snapd.",
    "sudo": "A command that lets a permitted user run a single command as root or another user. Required for system-modifying operations.",
    "systemd": "The init system and service manager used by AnduinOS; controls services, timers, and boot stages.",
    "chroot": "A Unix operation that changes the apparent root directory for a process; used during image building.",
    "Wayland": "The modern display server protocol used by default in AnduinOS; replaces X11.",
    "X11": "The legacy X Window System display protocol; still supported as a fallback for older applications.",
}


def show_shortcuts(parent: Gtk.Widget) -> None:
    """Show the keyboard shortcuts window (Gtk.ShortcutsWindow)."""
    builder_str = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="shortcuts_window">
    <property name="modal">True</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="section-name">shortcuts</property>
        <property name="max-height">12</property>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">General</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">app.quit</property>
                <property name="title" translatable="yes">Quit</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.fullscreen</property>
                <property name="title" translatable="yes">Toggle fullscreen</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.shortcuts</property>
                <property name="title" translatable="yes">Show this window</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">Navigation</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.go-home</property>
                <property name="title" translatable="yes">Go to home page</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.focus-search</property>
                <property name="title" translatable="yes">Focus search</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">Tools</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.refresh-docs</property>
                <property name="title" translatable="yes">Refresh docs from upstream</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.diagnostics</property>
                <property name="title" translatable="yes">System diagnostics</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.report-problem</property>
                <property name="title" translatable="yes">Report a problem</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="action-name">win.glossary</property>
                <property name="title" translatable="yes">Open glossary</property>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>
"""
    builder = Gtk.Builder()
    builder.set_translation_domain(APP_NAME)
    try:
        win = builder.add_from_string(builder_str)
        win = builder.get_object("shortcuts_window")
        if win is None:
            _log.error("Failed to build shortcuts window from string")
            return
        win.set_transient_for(parent)
        win.present()
    except Exception as exc:  # noqa: BLE001
        _log.error("Failed to show shortcuts window: %s", exc)
