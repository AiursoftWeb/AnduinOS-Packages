import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = ROOT / "assets" / "anduinos-oobe"


class PageReductionTests(unittest.TestCase):
    def test_accounts_and_backup_page_is_not_part_of_oobe(self):
        source = SCRIPT.read_text()

        self.assertNotIn("create_sync_page", source)
        self.assertNotIn("Connect & Protect Your Data", source)
        self.assertNotIn("Configure Automatic Snapshots", source)

    def test_removed_page_assets_are_not_packaged(self):
        icon_directory = ROOT / "resources" / "icons"

        for icon in (
            "deja-dup.svg",
            "disk-snapshots-manager.svg",
            "online-account.svg",
            "yast-upgrade.svg",
        ):
            with self.subTest(icon=icon):
                self.assertFalse((icon_directory / icon).exists())

    def test_update_page_moved_out_of_oobe(self):
        source = SCRIPT.read_text()

        self.assertNotIn("create_update_page", source)
        self.assertNotIn("Keep Your System Up to Date", source)
        self.assertNotIn("Switch to Fastest Mirror", source)


if __name__ == "__main__":
    unittest.main()
