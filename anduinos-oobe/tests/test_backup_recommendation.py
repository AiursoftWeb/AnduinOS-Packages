import importlib.machinery
import pathlib
import subprocess
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "assets" / "anduinos-oobe"
oobe = importlib.machinery.SourceFileLoader(
    "anduinos_oobe_backup", str(SCRIPT)
).load_module()


class BackupRecommendationTests(unittest.TestCase):
    def completed(self, stdout="", returncode=0):
        return subprocess.CompletedProcess([], returncode, stdout, "")

    def test_waypoint_requires_its_apt_package_and_a_btrfs_root(self):
        with (
            mock.patch.object(
                oobe, "_is_package_installed", return_value=True
            ) as package_installed,
            mock.patch.object(
                oobe.subprocess,
                "run",
                return_value=self.completed("btrfs\n"),
            ) as run,
        ):
            self.assertTrue(oobe.should_recommend_waypoint())

        package_installed.assert_called_once_with("anduinos-waypoint-gtk")
        run.assert_called_once_with(
            ["findmnt", "--noheadings", "--output", "FSTYPE", "--target", "/"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_waypoint_is_not_recommended_on_another_filesystem(self):
        with (
            mock.patch.object(oobe, "_is_package_installed", return_value=True),
            mock.patch.object(
                oobe.subprocess,
                "run",
                return_value=self.completed("ext4\n"),
            ),
        ):
            self.assertFalse(oobe.should_recommend_waypoint())

    def test_missing_waypoint_package_skips_the_filesystem_probe(self):
        with (
            mock.patch.object(oobe, "_is_package_installed", return_value=False),
            mock.patch.object(oobe.subprocess, "run") as run,
        ):
            self.assertFalse(oobe.should_recommend_waypoint())

        run.assert_not_called()

    def test_waypoint_card_opens_the_installed_application(self):
        with (
            mock.patch.object(oobe, "should_recommend_waypoint", return_value=True),
            mock.patch.object(oobe, "_", side_effect=lambda message: message),
        ):
            recommendation = oobe.get_backup_recommendation()

        self.assertEqual(recommendation["icon"], "org.anduinos.Waypoint")
        self.assertEqual(recommendation["title"], "Waypoint")
        self.assertEqual(recommendation["button"], "Configure Automatic Snapshots")
        self.assertEqual(recommendation["command"], ["/usr/bin/anduinos-waypoint-gtk"])

    def test_deja_dup_remains_the_fallback(self):
        with (
            mock.patch.object(oobe, "should_recommend_waypoint", return_value=False),
            mock.patch.object(oobe, "_", side_effect=lambda message: message),
        ):
            recommendation = oobe.get_backup_recommendation()

        self.assertEqual(recommendation["icon"], "deja-dup.svg")
        self.assertEqual(recommendation["title"], "System Backup")
        self.assertEqual(recommendation["button"], "Get Deja Dup")
        self.assertEqual(
            recommendation["command"],
            ["gnome-software", "--details=org.gnome.DejaDup.desktop"],
        )


if __name__ == "__main__":
    unittest.main()
