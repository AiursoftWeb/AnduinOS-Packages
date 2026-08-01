import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fakes import FakeRunner
from test_coexistence import windows_disk
from installer_core.esp import (
    capture_esp_vendor_tree,
    capture_preserved_esp_tree,
    inspect_esp_for_reuse,
    inspect_nvram,
    verify_nvram_entry,
    verify_preserved_esp_tree,
)


class EspInspectionTests(unittest.TestCase):
    def test_checks_fat_read_only_and_measures_free_space(self):
        esp = windows_disk(free_gib=24).partitions[0]
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            inspection = inspect_esp_for_reuse(
                esp,
                runner,
                scratch_root=Path(directory),
                statvfs=lambda _path: SimpleNamespace(
                    f_bavail=70,
                    f_frsize=1024 * 1024,
                ),
            )

        self.assertTrue(inspection.healthy)
        self.assertEqual(inspection.free_bytes, 70 * 1024 * 1024)
        commands = [item[0] for item in runner.commands]
        self.assertEqual(commands[0], ("fsck.fat", "-n", esp.identity.path))
        mount = commands[1]
        self.assertEqual(mount[:7], (
            "mount",
            "--read-only",
            "--types",
            "vfat",
            "--options",
            "nosuid,nodev,noexec",
            esp.identity.path,
        ))
        self.assertEqual(commands[2][0], "umount")
        self.assertEqual(runner.required, ["fsck.fat", "mount", "umount"])

    def test_unhealthy_fat_is_never_mounted(self):
        esp = windows_disk(free_gib=24).partitions[0]
        runner = FakeRunner()
        runner.outputs[("fsck.fat", "-n", esp.identity.path)] = (
            "",
            "filesystem has errors",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            inspection = inspect_esp_for_reuse(
                esp,
                runner,
                scratch_root=Path(directory),
            )

        self.assertFalse(inspection.healthy)
        self.assertEqual(inspection.free_bytes, 0)
        self.assertIn("filesystem has errors", inspection.reason)
        self.assertEqual(len(runner.commands), 1)

    def test_nvram_probe_is_read_only_and_fail_closed(self):
        available_runner = FakeRunner()
        self.assertTrue(inspect_nvram(available_runner).available)
        self.assertEqual(
            available_runner.commands[0][0],
            ("efibootmgr", "--verbose"),
        )

        unavailable_runner = FakeRunner()
        unavailable_runner.outputs[("efibootmgr", "--verbose")] = (
            "",
            "EFI variables are not supported",
            1,
        )
        result = inspect_nvram(unavailable_runner)
        self.assertFalse(result.available)
        self.assertIn("not supported", result.reason)

    def test_nvram_verification_binds_partition_and_vendor_loader(self):
        output = (
            "Boot0001* Windows Boot Manager "
            "HD(1,GPT,part-1,0x800,0x100000)/"
            "File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n"
            "Boot0007* AnduinOS "
            "HD(1,GPT,PART-1,0x800,0x100000)/"
            "File(\\EFI\\AnduinOS\\shimx64.efi)\n"
        )
        verify_nvram_entry(
            output,
            label="AnduinOS",
            partuuid="part-1",
            loader=r"\EFI\AnduinOS\shimx64.efi",
        )
        with self.assertRaisesRegex(RuntimeError, "was not created"):
            verify_nvram_entry(
                output,
                label="AnduinOS",
                partuuid="other-partition",
                loader=r"\EFI\AnduinOS\shimx64.efi",
            )

    def test_vendor_tree_can_change_but_windows_and_fallback_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            microsoft = root / "EFI/Microsoft/Boot/bootmgfw.efi"
            fallback = root / "EFI/BOOT/BOOTX64.EFI"
            vendor = root / "EFI/AnduinOS/shimx64.efi"
            for path, data in (
                (microsoft, b"windows"),
                (fallback, b"fallback"),
                (vendor, b"old-anduinos"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            snapshot = capture_preserved_esp_tree(root)
            vendor_snapshot = capture_esp_vendor_tree(root)
            self.assertEqual(
                tuple(item.relative_path for item in vendor_snapshot),
                ("EFI/AnduinOS/shimx64.efi",),
            )

            vendor.write_bytes(b"new-anduinos")
            verify_preserved_esp_tree(snapshot, root)

            microsoft.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "outside EFI/AnduinOS"):
                verify_preserved_esp_tree(snapshot, root)


if __name__ == "__main__":
    unittest.main()
