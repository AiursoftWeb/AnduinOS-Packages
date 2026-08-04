import unittest

from installer_core.layout import (
    build_erase_disk_layout,
    build_erase_disk_layout_spec,
)
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
        self.assertEqual(
            layout.partition("swap").size_mib,
            valid_plan().storage.swap_size_mib,
        )
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

    def test_ui_preview_builder_is_identical_to_plan_layout(self):
        plan = valid_plan()
        self.assertEqual(
            build_erase_disk_layout_spec(
                architecture=plan.platform.architecture,
                filesystem=plan.storage.filesystem,
                esp_size_mib=plan.storage.esp_size_mib,
                swap_size_mib=plan.storage.swap_size_mib,
            ),
            build_erase_disk_layout(plan),
        )


if __name__ == "__main__":
    unittest.main()
