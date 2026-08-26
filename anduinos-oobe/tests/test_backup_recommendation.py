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

    def test_live_detection_requires_the_dracut_runtime_contract(self):
        with (
            mock.patch.object(
                oobe.os.path,
                "isfile",
                side_effect=lambda path: path in {
                    "/run/anduinos-live/environment",
                    "/run/anduinos-live/rootfs.squashfs",
                },
            ),
            mock.patch.object(oobe.os.path, "isdir", return_value=True),
        ):
            self.assertTrue(oobe.is_live_environment())

        with mock.patch.object(oobe.os.path, "isfile", return_value=False):
            self.assertFalse(oobe.is_live_environment())

    def test_snapshots_manager_requires_its_package_and_a_btrfs_root(self):
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
            self.assertTrue(oobe.should_recommend_snapshots_manager())

        package_installed.assert_called_once_with("anduinos-btrfs-snapshots-manager")
        run.assert_called_once_with(
            ["findmnt", "--noheadings", "--output", "FSTYPE", "--target", "/"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_snapshots_manager_is_not_recommended_on_another_filesystem(self):
        with (
            mock.patch.object(oobe, "_is_package_installed", return_value=True),
            mock.patch.object(
                oobe.subprocess,
                "run",
                return_value=self.completed("ext4\n"),
            ),
        ):
            self.assertFalse(oobe.should_recommend_snapshots_manager())

    def test_missing_snapshots_manager_skips_the_filesystem_probe(self):
        with (
            mock.patch.object(oobe, "_is_package_installed", return_value=False),
            mock.patch.object(oobe.subprocess, "run") as run,
        ):
            self.assertFalse(oobe.should_recommend_snapshots_manager())

        run.assert_not_called()

    def test_snapshots_manager_card_opens_the_installed_application(self):
        with (
            mock.patch.object(
                oobe, "should_recommend_snapshots_manager", return_value=True
            ),
            mock.patch.object(oobe, "_", side_effect=lambda message: message),
        ):
            recommendation = oobe.get_backup_recommendation()

        self.assertEqual(recommendation["icon"], "disk-snapshots-manager.svg")
        self.assertEqual(recommendation["title"], "Disk Snapshots Manager")
        self.assertEqual(recommendation["button"], "Configure Automatic Snapshots")
        self.assertEqual(
            recommendation["command"], ["/usr/bin/anduinos-btrfs-snapshots-manager"]
        )

    def test_snapshots_manager_icon_is_bundled_by_oobe(self):
        expected = SCRIPT.parents[1] / "resources" / "icons" / "disk-snapshots-manager.svg"
        self.assertTrue(expected.is_file())
        self.assertEqual(pathlib.Path(oobe._icon(expected.name)).resolve(), expected.resolve())

    def test_deja_dup_remains_the_fallback(self):
        with (
            mock.patch.object(
                oobe, "should_recommend_snapshots_manager", return_value=False
            ),
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
