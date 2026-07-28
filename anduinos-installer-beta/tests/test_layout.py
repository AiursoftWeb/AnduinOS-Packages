import unittest

from installer_core.layout import build_erase_disk_layout
from installer_core.model import (
    Architecture,
    Filesystem,
    Firmware,
    SecureBoot,
)

from helpers import valid_plan


class LayoutTests(unittest.TestCase):
    def test_amd64_layout_supports_bios_and_uefi(self):
        layout = build_erase_disk_layout(valid_plan())
        self.assertEqual(layout.table, "gpt")
        self.assertEqual(
            [part.name for part in layout.partitions],
            ["bios-boot", "efi-system", "swap", "root"],
        )
        self.assertEqual(layout.partition("bios-boot").flags, ("bios_grub",))
        self.assertEqual(layout.partition("efi-system").size_mib, 1024)
        self.assertEqual(layout.partition("swap").size_mib, 4096)
        self.assertEqual(layout.partition("root").filesystem, "btrfs")

    def test_arm64_layout_is_uefi_only(self):
        plan = valid_plan(
            architecture=Architecture.ARM64,
            firmware=Firmware.UEFI,
            secure_boot=SecureBoot.ENABLED,
            filesystem=Filesystem.EXT4,
        )
        layout = build_erase_disk_layout(plan)
        self.assertEqual(
            [part.name for part in layout.partitions],
            ["efi-system", "swap", "root"],
        )
        self.assertEqual(layout.partition("root").filesystem, "ext4")


if __name__ == "__main__":
    unittest.main()
