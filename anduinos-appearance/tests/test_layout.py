import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock


SRC = pathlib.Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))

from anduinos_appearance import layout  # noqa: E402


class LayoutTests(unittest.TestCase):
    @staticmethod
    def completed(stdout="", returncode=0):
        return subprocess.CompletedProcess([], returncode, stdout, "")

    def run_with_dconf(self, style, position="bottom", screen_height=1080):
        def fake_run(command, **kwargs):
            if command[:2] == ["dconf", "read"]:
                if command[2] == f"{layout.DTP}/panel-anchors":
                    return self.completed("{'DP-1': {}}")
                if command[2] == f"{layout.DTP}/panel-sizes":
                    return self.completed("{'0': 52, 'DP-1': 60}")
            return self.completed()

        with (
            mock.patch.object(layout.subprocess, "run", side_effect=fake_run) as run,
            mock.patch.object(
                layout, "_smallest_monitor_height", return_value=screen_height
            ),
        ):
            result = layout.apply_style_and_position(style, position)
        return result, [call.args[0] for call in run.call_args_list]

    def assert_write(self, commands, key, value):
        self.assertIn(["dconf", "write", key, value], commands)

    def test_eleven_uses_650_height_and_windows_grouping(self):
        result, commands = self.run_with_dconf("eleven")

        self.assertTrue(result)
        self.assert_write(commands, f"{layout.ARC}/menu-height", "650")
        self.assert_write(commands, f"{layout.ARC}/menu-layout", "'11'")
        self.assertIn(
            ["dconf", "reset", f"{layout.ARC}/menu-arrow-rise"], commands
        )
        self.assert_write(commands, f"{layout.DTP}/group-apps", "true")
        self.assert_write(
            commands, f"{layout.DTP}/group-apps-use-launchers", "true"
        )

    def test_classic_uses_785_height_without_overwriting_grouping(self):
        result, commands = self.run_with_dconf("classic")

        self.assertTrue(result)
        self.assert_write(commands, f"{layout.ARC}/menu-height", "785")
        self.assert_write(commands, f"{layout.ARC}/menu-layout", "'arcmenu'")
        self.assert_write(
            commands, f"{layout.ARC}/menu-arrow-rise", "(true, -8)"
        )
        self.assertNotIn(
            ["dconf", "write", f"{layout.DTP}/group-apps", "true"], commands
        )

    def test_seperated_uses_classic_menu_height(self):
        result, commands = self.run_with_dconf("seperated")

        self.assertTrue(result)
        self.assert_write(commands, f"{layout.ARC}/menu-height", "785")
        self.assert_write(
            commands, f"{layout.ARC}/menu-arrow-rise", "(true, -8)"
        )

    def test_classic_menu_height_scales_with_screen_height(self):
        self.assertEqual(layout.calculate_menu_height("classic", 600), 650)
        self.assertEqual(layout.calculate_menu_height("classic", 768), 650)
        self.assertEqual(layout.calculate_menu_height("classic", 800), 664)
        self.assertEqual(layout.calculate_menu_height("classic", 900), 707)
        self.assertEqual(layout.calculate_menu_height("classic", 1080), 785)
        self.assertEqual(layout.calculate_menu_height("classic", 10000), 785)

    def test_eleven_menu_height_always_stays_at_650(self):
        self.assertEqual(layout.calculate_menu_height("eleven", 600), 650)
        self.assertEqual(layout.calculate_menu_height("eleven", 900), 650)
        self.assertEqual(layout.calculate_menu_height("eleven", 10000), 650)

    def test_apply_uses_smallest_monitor_height(self):
        result, commands = self.run_with_dconf("classic", screen_height=900)

        self.assertTrue(result)
        self.assert_write(commands, f"{layout.ARC}/menu-height", "707")

    def test_monitor_ids_and_existing_panel_sizes_are_preserved(self):
        result, commands = self.run_with_dconf("classic")

        self.assertTrue(result)
        panel_size_write = next(
            command
            for command in commands
            if command[:3] == ["dconf", "write", f"{layout.DTP}/panel-sizes"]
        )
        sizes = json.loads(panel_size_write[3].strip("'"))
        self.assertEqual(sizes["0"], 52)
        self.assertEqual(sizes["DP-1"], 60)

    def test_write_failure_is_reported(self):
        def fake_run(command, **kwargs):
            if command[:2] == ["dconf", "read"]:
                return self.completed("{}")
            if command[2] == f"{layout.ARC}/menu-height":
                raise subprocess.CalledProcessError(1, command)
            return self.completed()

        with (
            mock.patch.object(layout.subprocess, "run", side_effect=fake_run),
            mock.patch.object(layout, "_smallest_monitor_height", return_value=1080),
        ):
            self.assertFalse(layout.apply_style_and_position("eleven", "bottom"))


if __name__ == "__main__":
    unittest.main()
