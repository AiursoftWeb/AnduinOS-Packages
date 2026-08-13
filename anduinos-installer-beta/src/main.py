"""Entry point for the unprivileged AnduinOS GTK4 installer (beta).

Polkit delegates exact read-only storage geometry to a narrow helper. The UI
passes one validated plan to a separate root executor only when installation
begins.
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

from i18n import _, N_
from languages import default_timezone, detect_system_language
from pages import build_all_pages
from frontend import guided_storage_enabled
from installer_core.hostnames import (
    detect_device_type,
    generate_random_suffix,
    suggest_hostname,
)
from ui import load_visual_style


APP_ID = "com.anduinos.InstallerBeta"
ICON_NAME = "anduinos-installer-beta"


class InstallerApplication(Adw.Application):
    """GTK4 application for the AnduinOS installer."""

    def __init__(
        self,
        development_mode: bool = False,
    ):
        super().__init__(application_id=APP_ID)
        detected_language = detect_system_language()
        hostname_device_type = detect_device_type()
        hostname_random_suffix = generate_random_suffix()
        # Shared state — every page reads/writes this dict.
        self.shared_state: dict[str, object] = {
            "lang": detected_language.code,
            "keyboard": detected_language.keyboard,
            "disk": "",
            "disk_size": "",
            "disk_size_bytes": 0,
            "disk_model": "",
            "disk_stable_id": "",
            "disk_topology_digest": "",
            "disk_windows_detected": False,
            "disk_bitlocker_detected": False,
            "disk_has_existing_partitions": False,
            "disk_erase_available": False,
            "storage_strategy": "",
            "storage_mode": "erase-disk",
            "guided_extent_id": "",
            "guided_esp_partuuid": "",
            "guided_storage_preview_model": None,
            "guided_storage_enabled": guided_storage_enabled(),
            "filesystem": "btrfs",
            "username": "",
            "full_name": "",
            "password": "",
            "password_confirmation": "",
            "sudo_without_password": False,
            "automatic_login": False,
            "ssh_password_login": False,
            "hostname": suggest_hostname(
                "", hostname_device_type, hostname_random_suffix
            ),
            "_hostname_device_type": hostname_device_type,
            "_hostname_random_suffix": hostname_random_suffix,
            "_hostname_user_edited": False,
            "timezone": default_timezone(detected_language.code),
            "locale": detected_language.locale,
            "installation_running": False,
            "development_mode": development_mode,
            "install_updates": True,
            "install_third_party_drivers": False,
            "install_multimedia_codecs": False,
            "input_methods": detected_language.default_input_methods,
            "_preferred_install_updates": True,
            "_preferred_install_third_party_drivers": False,
            "_preferred_install_multimedia_codecs": False,
            "_preferred_input_methods": detected_language.default_input_methods,
        }

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(ICON_NAME)

    def do_activate(self):
        """Build and present the main window."""
        try:
            lang = str(self.shared_state["lang"])
            title_message = (
                N_("AnduinOS Installer (Development)")
                if self.shared_state["development_mode"]
                else N_("AnduinOS Installer (Beta)")
            )
            title = _(title_message, lang)
            win = Adw.ApplicationWindow(application=self,
                                        title=title,
                                        default_width=960,
                                        default_height=680,
                                        width_request=720,
                                        height_request=520)
            win.add_css_class("installer-window")
            load_visual_style(win.get_display())

            # ToolbarView: header bar (draggable, close button) + content
            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            win_title = Adw.WindowTitle(title=title)
            header.set_title_widget(win_title)
            toolbar.add_top_bar(header)

            def _set_window_language(language: str):
                localized_title = _(title_message, language)
                win.set_title(localized_title)
                win_title.set_title(localized_title)

            self.shared_state["_set_window_language"] = _set_window_language

            self._nav = Adw.NavigationView(animate_transitions=True)
            toolbar.set_content(self._nav)
            win.set_content(toolbar)

            def _protect_install(_window):
                if not self.shared_state.get("installation_running"):
                    return False
                dialog = Adw.MessageDialog(
                    transient_for=win,
                    heading=_("Installation in progress", lang),
                    body=_(
                        "The installer cannot be closed while it is modifying "
                        "the target disk.",
                        lang,
                    ),
                )
                dialog.add_response(
                    "ok", _("Continue Installation", lang)
                )
                dialog.present()
                return True

            win.connect("close-request", _protect_install)

            pages = build_all_pages(self.shared_state, self._nav)
            self._nav.push(pages[0])

            win.present()
        except Exception:
            import traceback
            traceback.print_exc()
            raise


def main():
    """Application entry point called by the shell launcher."""
    development_mode = (
        "--development" in sys.argv
        or os.environ.get("ANDUINOS_INSTALLER_DEVELOPMENT") == "1"
    )
    argv = [argument for argument in sys.argv if argument != "--development"]
    app = InstallerApplication(development_mode)
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
