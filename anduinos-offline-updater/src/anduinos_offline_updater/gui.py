"""Unprivileged GTK frontend for the read-only bundle inspector."""
import gettext
import json
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

PRO_PACKAGES_URL = "https://www.anduinos.com/pro/offline-update-package"
STYLE = """
.offline-updater .title-1 { font-size: 29px; font-weight: 800; }
.offline-updater button.large-button { padding: 12px 32px; font-size: 14px; }
"""
SOURCE_LOCALES = Path(__file__).resolve().parents[2] / "locale"
_ = gettext.translation("anduinos-offline-updater",
                        localedir=str(SOURCE_LOCALES) if SOURCE_LOCALES.is_dir() else "/usr/share/locale",
                        fallback=True).gettext


def text(value, *classes):
    widget = Gtk.Label(label=value, wrap=True, justify=Gtk.Justification.CENTER)
    for name in classes:
        widget.add_css_class(name)
    return widget


class UpdateWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title=_("Offline Updater"),
                         default_width=760, default_height=800)
        self.add_css_class("offline-updater")
        self.busy = False
        self.closed = False
        self.report = None
        self.connect("close-request", self.on_close)
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle.new(_("Offline Updater"), "AnduinOS"))
        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=_("Choose another file"))
        self.back.connect("clicked", lambda *_: self.home())
        header.pack_start(self.back)
        toolbar.add_top_bar(header)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        toolbar.set_content(self.scroller)
        self.clamp = Adw.Clamp(maximum_size=580, tightening_threshold=580)
        self.clamp.set_margin_start(24)
        self.clamp.set_margin_end(24)
        self.scroller.set_child(self.clamp)
        footer = text(_("Preview only. Installing updates is not available in this build."), "caption", "dim-label")
        footer.set_margin_start(24)
        footer.set_margin_end(24)
        footer.set_margin_top(10)
        footer.set_margin_bottom(16)
        toolbar.add_bottom_bar(footer)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        self.home()

    def page(self, title, description=None, icon=None):
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                               valign=Gtk.Align.CENTER, vexpand=True)
        self.content.set_margin_top(32)
        self.content.set_margin_bottom(32)
        self.clamp.set_child(self.content)
        self.scroller.get_vadjustment().set_value(0)
        if icon:
            image = Gtk.Image.new_from_icon_name(icon)
            image.set_pixel_size(96)
            image.add_css_class("accent")
            image.set_margin_bottom(12)
            self.content.append(image)
        self.content.append(text(title, "title-1"))
        if description:
            self.content.append(text(description, "dim-label"))

    def home(self):
        if self.busy:
            return
        self.report = None
        self.back.set_visible(False)
        self.page(_("Update from a file"),
                  _("Choose an AnduinOS update package.\nNo internet connection needed to check the file."),
                  "folder-download-symbolic")
        choose = Gtk.Button(label=_("Choose Update File"), halign=Gtk.Align.CENTER)
        choose.add_css_class("suggested-action")
        choose.add_css_class("pill")
        choose.add_css_class("large-button")
        choose.set_margin_top(12)
        choose.connect("clicked", self.choose_file)
        self.content.append(choose)
        self.content.append(text(_("or drop an .atgz file here"), "caption", "dim-label"))
        notice = text(_("Official .atgz update packages are a paid AnduinOS Pro benefit."), "caption", "dim-label")
        notice.set_margin_top(28)
        self.content.append(notice)
        self.package_link = Gtk.LinkButton.new_with_label(PRO_PACKAGES_URL, _("Get updates from your Pro benefits center"))
        self.package_link.set_halign(Gtk.Align.CENTER)
        self.package_link.get_child().set_wrap(True)
        self.package_link.get_child().set_justify(Gtk.Justification.CENTER)
        self.package_link.connect("activate-link", self.open_packages)
        self.content.append(self.package_link)

    def open_packages(self, *_):
        launcher = Gtk.UriLauncher.new(PRO_PACKAGES_URL)
        launcher.launch(self, None, self.link_opened)
        return True

    def link_opened(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            if not self.closed:
                dialog = Adw.AlertDialog(heading=_("Could not open the website"), body=PRO_PACKAGES_URL)
                dialog.add_response("close", _("Close"))
                dialog.present(self)

    def choose_file(self, *_args):
        if self.busy:
            return
        filters = Gio.ListStore.new(Gtk.FileFilter)
        file_filter = Gtk.FileFilter(name=_("AnduinOS update packages"))
        file_filter.add_pattern("*.atgz")
        filters.append(file_filter)
        dialog = Gtk.FileDialog(title=_("Choose Update File"), filters=filters, modal=True)
        dialog.open(self, None, self.file_chosen)

    def file_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED) and not self.closed:
                self.error(_("Could not open the file"))
            return
        if not self.closed:
            self.inspect_files([file])

    def on_drop(self, _target, value, _x, _y):
        return self.inspect_files(value.get_files())

    def inspect_files(self, files):
        if self.busy or self.closed:
            return False
        if len(files) != 1 or not files[0].is_native() or not files[0].get_path():
            self.error(_("Choose one local .atgz file."))
            return False
        file = Path(files[0].get_path())
        if file.suffix.lower() != ".atgz" or not file.is_file():
            self.error(_("Choose one local .atgz file."))
            return False
        self.busy = True
        self.back.set_visible(False)
        self.page(_("Checking your update…"), _("Verifying the signature and checking your system. This may take a few minutes."))
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, width_request=40, height_request=40)
        self.content.append(spinner)
        # A separate process isolates python-apt's global configuration from GTK.
        # Nothing is elevated, and the child uses only the existing read-only CLI.
        app = self.get_application()
        try:
            cache = Path(GLib.get_user_cache_dir()) / "anduinos-offline-updater"
            cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            process = Gio.Subprocess.new(
                [sys.executable, "-m", "anduinos_offline_updater.cli", "inspect", str(file),
                 "--work-directory", str(cache), "--json"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        except (OSError, GLib.Error) as error:
            self.busy = False
            self.error(str(error))
            return False
        # Let verification clean its temporary files even if the window closes.
        app.hold()
        process.communicate_utf8_async(None, None, self.inspected, (app, file))
        return True

    def inspected(self, process, result, data):
        app, file = data
        self.busy = False
        try:
            _communicated, output, _stderr = process.communicate_utf8_finish(result)
            if not process.get_if_exited() or process.get_exit_status() not in (0, 2):
                raise ValueError(_("The update could not be checked. Try choosing the file again."))
            report = json.loads(output)
            if report.get("read_only") is not True or report.get("can_install") is not False:
                raise ValueError(_("The update could not be checked. Try choosing the file again."))
            if not self.closed:
                self.show_report(file, report)
        except (GLib.Error, ValueError, TypeError, KeyError, AttributeError) as error:
            if not self.closed:
                self.error(str(error))
        finally:
            app.release()

    def error(self, reason):
        self.report = None
        self.back.set_visible(True)
        self.page(_("Cannot preview this update"), _("No system packages were changed."), "dialog-warning-symbolic")
        # Treat bundle filenames and verifier diagnostics as plain text, never markup.
        detail = text(reason, "dim-label")
        detail.set_selectable(True)
        self.content.append(detail)

    def show_report(self, file, report):
        state = report["state"]
        if state == "blocked":
            self.error("\n".join(report.get("blockers", [])))
            return
        headings = {"update-available": _("Review your update"),
                    "already-installed": _("You already have this update"),
                    "updater-update-required": _("A newer updater is required")}
        if state not in headings:
            raise ValueError(_("The update could not be checked. Try choosing the file again."))
        self.report = report
        self.back.set_visible(True)
        self.page(headings[state])
        versions = Gtk.Box(spacing=24, homogeneous=True)
        for caption, value in ((_("Current version"), report["current_version"]),
                               (_("Update to"), report["target_version"])):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            column.append(text(caption, "dim-label"))
            column.append(text(value, "title-1"))
            versions.append(column)
        self.content.append(versions)
        name = text(file.name, "caption", "dim-label")
        name.set_selectable(True)
        self.content.append(name)
        changes = sum(len(stage["changes"]) for stage in report["stages"])
        space = report["space"]
        group = Adw.PreferencesGroup()
        snapshot = report.get("snapshot", {}).get("state")
        snapshot_text = {"disabled": _("Disabled"), "unavailable": _("Unavailable")}.get(snapshot, _("Check required"))
        def size(key):
            value = space.get(key)
            return GLib.format_size(value) if isinstance(value, int) and value >= 0 else _("Unknown")
        for title, value in ((_("Package changes"), str(changes)),
                             (_("Temporary storage"), size("staging_bytes")),
                             (_("Package expansion estimate"), size("package_expansion_upper_estimate_bytes")),
                             (_("System snapshot"), snapshot_text)):
            row = Adw.ActionRow(title=title)
            row.add_suffix(text(value, "dim-label"))
            group.add(row)
        self.content.append(group)
        self.content.append(text(_("Installation space and snapshot availability must be checked again before installing."), "caption", "dim-label"))
        details = Gtk.Expander(label=_("Technical details"))
        detail = Gtk.Label(label=json.dumps(report, indent=2, ensure_ascii=False),
                           wrap=True, selectable=True, xalign=0)
        detail.add_css_class("monospace")
        detail.add_css_class("caption")
        details.set_child(detail)
        self.content.append(details)

    def on_close(self, *_):
        self.closed = True
        return False


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.anduinos.offlineupdater")

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_data(STYLE.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        window = self.get_active_window() or UpdateWindow(self)
        window.present()


def main():
    return Application().run([])
