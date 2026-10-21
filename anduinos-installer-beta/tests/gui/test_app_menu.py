"""Native application help remains available independently of installation."""

import unittest
from unittest.mock import patch

from app_menu import InstallerMenu, SOURCE, WEBSITE, ISSUES, installer_version
from gi.repository import Adw, Gtk


class InstallerMenuTests(unittest.TestCase):
    def setUp(self):
        self.window = Adw.Window()
        self.addCleanup(self.window.destroy)
        self.shared = {"lang": "en_US", "installation_running": True}
        self.menu = InstallerMenu(self.window, self.shared)
        self.window.set_content(self.menu)

    def labels(self):
        model = self.menu.get_menu_model()
        return [model.get_item_attribute_value(index, "label", None).unpack()
                for index in range(model.get_n_items())]

    def test_menu_relocalizes_and_actions_stay_enabled_during_installation(self):
        self.assertEqual(self.labels(),
                         ["About", "View Source", "Official Website", "Report an Issue"])
        self.shared["lang"] = "zh_CN"
        self.menu.refresh_language("zh_CN")
        self.assertEqual(self.labels(), ["关于", "查看源码", "官方网站", "报告问题"])
        self.assertEqual(self.menu.get_tooltip_text(), "主菜单")
        for name in ("about", "source", "website", "issues"):
            self.assertTrue(self.menu.actions.lookup_action(name).get_enabled())

    def test_link_actions_use_expected_destinations(self):
        with patch.object(self.menu, "open_uri") as opened:
            for name, url in (("source", SOURCE), ("website", WEBSITE), ("issues", ISSUES)):
                self.menu.actions.lookup_action(name).activate(None)
                opened.assert_called_with(url)

    def test_about_uses_native_dialog_with_version_and_package_identity(self):
        dialogs = []

        def present(dialog, parent):
            self.assertIs(parent, self.window)
            dialogs.append(dialog)

        with patch.object(Adw.AboutDialog, "present", present):
            self.menu.actions.lookup_action("about").activate(None)
        self.assertEqual(len(dialogs), 1)
        dialog = dialogs[0]
        self.assertEqual(dialog.get_application_name(), "AnduinOS Installer")
        self.assertEqual(dialog.get_application_icon(), "anduinos-installer-beta")
        self.assertEqual(dialog.get_developer_name(), "AnduinOS Team")
        self.assertEqual(dialog.get_version(), installer_version())
        self.assertNotEqual(dialog.get_version(), "—")
        self.assertNotIn("$(", dialog.get_version())
        self.assertEqual(dialog.get_license_type(), Gtk.License.GPL_3_0)
        self.assertEqual(dialog.get_website(), WEBSITE)
        self.assertEqual(dialog.get_issue_url(), ISSUES)
