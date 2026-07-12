"""Entry point for the AnduinOS GTK4 installer (beta).

Run as root on a Live ISO.  The shell launcher handles privilege
escalation — this module just starts the GTK application.
"""

import sys
import os

# Allow absolute imports from the install directory whether run directly
# or as a module.
_install_dir = os.path.dirname(os.path.abspath(__file__))
if _install_dir not in sys.path:
    sys.path.insert(0, _install_dir)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gio, GLib

from pages import build_all_pages


APP_ID = "com.anduinos.InstallerBeta"


class InstallerApplication(Adw.Application):
    """GTK4 application for the AnduinOS installer."""

    def __init__(self):
        super().__init__(application_id=APP_ID)
        # Shared state — every page reads/writes this dict.
        self.shared_state: dict[str, object] = {
            "lang": "en",
            "keyboard": "us",
            "disk": "",
            "disk_size": "",
            "disk_model": "",
            "username": "",
            "full_name": "",
            "password": "",
            "hostname": "anduinos",
            "timezone": "America/New_York",
        }

    def do_startup(self):
        Adw.Application.do_startup(self)

    def do_activate(self):
        """Build and present the main window."""
        try:
            win = Adw.ApplicationWindow(application=self,
                                        title="AnduinOS Installer (Beta)",
                                        default_width=960,
                                        default_height=640)

            # ToolbarView: header bar (draggable, close button) + content
            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            win_title = Adw.WindowTitle(title="AnduinOS Installer (Beta)")
            header.set_title_widget(win_title)
            toolbar.add_top_bar(header)

            self._nav = Adw.NavigationView()
            toolbar.set_content(self._nav)
            win.set_content(toolbar)

            pages = build_all_pages(self.shared_state, self._nav)
            self._nav.push(pages[0])

            win.present()
        except Exception:
            import traceback
            traceback.print_exc()
            raise


def main():
    """Application entry point called by the shell launcher."""
    app = InstallerApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
