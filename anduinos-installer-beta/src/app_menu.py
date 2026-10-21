"""Application help menu, independent of installation state."""

from functools import lru_cache
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from i18n import _, N_

WEBSITE = "https://www.anduinos.com"
SOURCE = "https://github.com/AiursoftWeb/AnduinOS-Packages"
ISSUES = SOURCE + "/issues"


@lru_cache(maxsize=1)
def installer_version():
    project = Path(__file__).resolve().parent.parent / "anduinos-installer-beta.aosproj"
    if project.is_file():
        try:
            value = ET.parse(project).findtext(".//PackageVersion")
            if value:
                return value.split("+", 1)[0]
        except (OSError, ET.ParseError):
            pass
    try:
        result = subprocess.run(
            ["dpkg-query", "--show", "--showformat=${Version}", "anduinos-installer-beta"],
            capture_output=True, text=True, timeout=2, check=True,
        )
        return result.stdout.strip() or "—"
    except (OSError, subprocess.SubprocessError):
        return "—"


class InstallerMenu(Gtk.MenuButton):
    def __init__(self, window, shared):
        super().__init__(icon_name="view-more-symbolic")
        self.window = window
        self.shared = shared
        self.actions = Gio.SimpleActionGroup()
        self.items = (
            ("about", N_("About"), self.show_about),
            ("source", N_("View Source"), lambda: self.open_uri(SOURCE)),
            ("website", N_("Official Website"), lambda: self.open_uri(WEBSITE)),
            ("issues", N_("Report an Issue"), lambda: self.open_uri(ISSUES)),
        )
        for name, _label, callback in self.items:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, cb=callback: cb())
            self.actions.add_action(action)
        window.insert_action_group("help", self.actions)
        self.refresh_language(str(shared["lang"]))

    def refresh_language(self, language):
        menu = Gio.Menu()
        for name, label, _callback in self.items:
            menu.append(_(label, language), "help." + name)
        self.set_menu_model(menu)
        self.set_tooltip_text(_("Main Menu", language))

    def show_about(self):
        language = str(self.shared["lang"])
        dialog = Adw.AboutDialog(
            application_name=_("AnduinOS Installer", language),
            application_icon="anduinos-installer-beta",
            developer_name="AnduinOS Team",
            version=installer_version(),
            website=WEBSITE,
            issue_url=ISSUES,
            license_type=Gtk.License.GPL_3_0,
        )
        dialog.add_link(_("View Source", language), SOURCE)
        dialog.present(self.window)

    def open_uri(self, uri):
        def opened(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error as error:
                if error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    return
                language = str(self.shared["lang"])
                dialog = Adw.MessageDialog(
                    transient_for=self.window,
                    heading=_("Unavailable: {error}", language).format(error=error.message),
                    body=uri,
                )
                dialog.add_response("ok", _("OK", language))
                dialog.present()
        Gtk.UriLauncher.new(uri).launch(self.window, None, opened)
