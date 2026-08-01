import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).parents[1]
ICONS = ROOT / "assets" / "icons"


class InstallerVisualAssetTests(unittest.TestCase):
    def test_every_wizard_illustration_is_a_parseable_local_svg(self):
        expected = {
            "welcome.svg",
            "language.svg",
            "keyboard.svg",
            "updates.svg",
            "disk.svg",
            "timeback.svg",
            "coexistence.svg",
            "secure-boot.svg",
            "account.svg",
            "timezone.svg",
            "review.svg",
            "advanced.svg",
            "btrfs.svg",
            "ext4.svg",
            "flashing-disk.svg",
            "how-should-use.svg",
            "one-single-disk.svg",
            "select-installation-disk.svg",
        }
        self.assertEqual(
            {path.name for path in ICONS.glob("*.svg")},
            expected,
        )
        for name in sorted(expected):
            with self.subTest(name=name):
                root = ET.parse(ICONS / name).getroot()
                self.assertTrue(root.tag.endswith("svg"))

    def test_stylesheet_defines_the_shared_visual_contract(self):
        style = (ROOT / "assets" / "style.css").read_text()
        for selector in (
            ".installer-hero",
            ".installer-card",
            ".disk-card-list",
            ".partition-chip",
            ".strategy-card",
            ".wizard-navigation",
            ".wizard-dot-active",
            ".installer-progress",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, style)

    def test_copied_illustrations_have_package_local_provenance(self):
        provenance = (ICONS / "README.md").read_text()
        for name in sorted(path.name for path in ICONS.glob("*.svg")):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", provenance)
        self.assertIn("GPL-3.0", provenance)

    def test_storage_cards_expose_layout_without_redundant_disk_copy(self):
        pages = (ROOT / "src/pages.py").read_text()
        for fragment in (
            "disk.partitions",
            "disk.free_extents",
            "extent.size_bytes >= _LAYOUT_FREE_SPACE_MINIMUM_BYTES",
            "partition.filesystem_type",
            "row.append(icon_picture(icon, 56))",
            '        "btrfs",',
            '        "ext4",',
            '        "advanced",',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, pages)
        self.assertNotIn(
            "Only this disk can be partitioned or formatted",
            pages,
        )


if __name__ == "__main__":
    unittest.main()
