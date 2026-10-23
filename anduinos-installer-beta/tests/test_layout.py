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
    def test_recovery_volume_is_btrfs_only_and_never_permanently_mounted(self):
        for architecture in Architecture:
            for filesystem in (Filesystem.BTRFS, Filesystem.EXT4):
                with self.subTest(architecture=architecture, filesystem=filesystem):
                    layout = build_erase_disk_layout(valid_plan(
                        architecture=architecture, filesystem=filesystem,
                    ))
                    recovery = [p for p in layout.partitions if p.name == "recovery"]
                    self.assertEqual(len(recovery), int(filesystem is Filesystem.BTRFS))
                    if recovery:
                        self.assertEqual(recovery[0].size_mib, 256)
                        self.assertEqual(recovery[0].filesystem, "ext4")
                        self.assertIsNone(recovery[0].mount_point)
                        self.assertEqual(recovery[0].end_mib, layout.partition("root").start_mib)

    def test_amd64_layout_supports_bios_and_uefi(self):
        layout = build_erase_disk_layout(valid_plan())
        self.assertEqual(layout.table, "gpt")
        self.assertEqual(
            [part.name for part in layout.partitions],
            ["bios-boot", "efi-system", "swap", "recovery", "root"],
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

    def test_zero_disk_swap_omits_partition_and_moves_root_forward(self):
        amd64 = build_erase_disk_layout(valid_plan(swap_size_mib=0))
        self.assertEqual(
            [part.name for part in amd64.partitions],
            ["bios-boot", "efi-system", "recovery", "root"],
        )
        self.assertEqual(amd64.partition("root").number, 4)
        arm64 = build_erase_disk_layout(
            valid_plan(
                architecture=Architecture.ARM64,
                swap_size_mib=0,
            )
        )
        self.assertEqual(
            [part.name for part in arm64.partitions],
            ["efi-system", "recovery", "root"],
        )
        self.assertEqual(arm64.partition("root").number, 3)


if __name__ == "__main__":
    unittest.main()
