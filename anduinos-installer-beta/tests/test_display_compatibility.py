import tempfile
import unittest
from pathlib import Path

from installer_core.display_compatibility import (
    display_compatibility_entry,
    intel_integrated_graphics_visible,
)


class DisplayCompatibilityTests(unittest.TestCase):
    def test_detects_unbound_intel_igd_on_root_bus(self):
        with tempfile.TemporaryDirectory() as directory:
            devices = Path(directory)
            arc = devices / "0000:03:00.0"
            arc.mkdir()
            (arc / "vendor").write_text("0x8086\n")
            (arc / "class").write_text("0x030000\n")
            self.assertFalse(intel_integrated_graphics_visible(devices))

            igd = devices / "0000:00:02.0"
            igd.mkdir()
            (igd / "vendor").write_text("0x8086\n")
            (igd / "class").write_text("0x020000\n")
            self.assertFalse(intel_integrated_graphics_visible(devices))
            (igd / "class").write_text("0x030000\n")
            self.assertTrue(intel_integrated_graphics_visible(devices))

    def test_clones_default_kernel_and_preserves_unlock_and_initramfs(self):
        for version in ("7.0.0-44-generic", "7.0.0-45-generic"):
            with self.subTest(version=version):
                normal_line = (
                    f"  linux /@root/boot/vmlinuz-{version} root=UUID=installed "
                    "ro rootflags=subvol=@root rd.luks.uuid=ab12 quiet splash"
                )
                initrd = f"  initrd /boot/intel-ucode.img /@root/boot/initrd.img-{version}"
                entries = "\n".join((
                    "menuentry 'AnduinOS' --id 'gnulinux-simple-installed' {",
                    "  savedefault",
                    "  recordfail",
                    "  if [ -z \"$anduinos_unlocked\" ]; then",
                    "    anduinos_unlock",
                    "  fi",
                    f"{normal_line} i915.enable_psr=1 xe.enable_psr=2",
                    initrd,
                    "}",
                    "submenu 'Advanced options for AnduinOS' {",
                    "  menuentry 'Recovery' {",
                    "    linux /boot/vmlinuz-old ro single nomodeset",
                    "  }",
                    "}",
                ))
                fallback = display_compatibility_entry(entries)
                self.assertIn("'anduinos-display-compatibility' {", fallback)
                self.assertIn(f"{normal_line} i915.enable_psr=0 xe.enable_psr=0\n", fallback)
                self.assertIn(f"{initrd}\n", fallback)
                self.assertIn("    anduinos_unlock\n", fallback)
                self.assertNotIn("savedefault", fallback)
                self.assertNotIn("vmlinuz-old", fallback)
                self.assertNotIn("enable_psr=1", fallback)
                self.assertNotIn("enable_psr=2", fallback)

    def test_ignores_incomplete_or_missing_linux_entries(self):
        self.assertEqual(display_compatibility_entry(""), "")
        self.assertEqual(display_compatibility_entry("menuentry 'AnduinOS' {\n  linux /vmlinuz"), "")
