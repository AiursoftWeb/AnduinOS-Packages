from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ArcMenuDefaultsTests(unittest.TestCase):
    def test_control_panel_follows_activities_overview(self):
        defaults = (ROOT / "dconf/10-arcmenu.conf").read_text()
        application_shortcuts = next(
            line
            for line in defaults.splitlines()
            if line.startswith("application-shortcuts=")
        )
        activities = "{'id': 'ArcMenu_ActivitiesOverview'"
        control_panel = "{'id': 'com.anduinos.ControlPanel.desktop'}"
        self.assertEqual(application_shortcuts.count(control_panel), 1)
        self.assertLess(
            application_shortcuts.index(activities),
            application_shortcuts.index(control_panel),
        )

    def test_control_panel_is_suggested_without_pulling_the_app_stack(self):
        project = (ROOT / "gnome-shell-extension-arcmenu.aosproj").read_text()
        self.assertIn(
            '<Suggest Include="anduinos-control-panel" />', project
        )
        self.assertNotIn(
            '<Recommend Include="anduinos-control-panel" />', project
        )

    def test_control_panel_replaces_appearance_in_pinned_apps(self):
        defaults = (ROOT / "dconf/10-arcmenu.conf").read_text()
        pinned_apps = next(
            line
            for line in defaults.splitlines()
            if line.startswith("pinned-apps=")
        )
        control_panel = "{'id': 'com.anduinos.ControlPanel.desktop'}"
        self.assertEqual(pinned_apps.count(control_panel), 1)
        self.assertNotIn("{'id': 'anduinos-appearance.desktop'}", pinned_apps)
        self.assertTrue(pinned_apps.endswith(f"{control_panel}]"))


if __name__ == "__main__":
    unittest.main()
