"""Fast source checks; the release gate also installs the built DEB in a VM."""

import os
import re
import stat
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parent.parent
THEME = PACKAGE / "assets/theme"
POSTINST = PACKAGE / "scripts/postinst.sh"
POSTRM = PACKAGE / "scripts/postrm.sh"


def fake_command(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class PackageContractTests(unittest.TestCase):
    def test_theme_is_self_contained_and_secure_boot_safe(self) -> None:
        config = (THEME / "theme.txt").read_text(encoding="utf-8")
        self.assertIn('desktop-image: "background.png"', config)
        self.assertTrue((THEME / "background.png").is_file())
        with (THEME / "background.png").open("rb") as background:
            self.assertEqual(background.read(16)[:8], b"\x89PNG\r\n\x1a\n")
            width, height = struct.unpack(">II", background.read(8))
        self.assertEqual(width * 9, height * 16)
        self.assertIn('desktop-image-scale-method: "crop"', config)
        self.assertIn('desktop-image-h-align: "left"', config)
        self.assertTrue((THEME / "select_c.png").is_file())
        self.assertFalse(list(THEME.rglob("*.pf2")))
        self.assertNotIn("font:", config)
        self.assertNotIn("item_font", config)
        live = (THEME / "live-grub.cfg").read_text(encoding="utf-8")
        self.assertIn('if [ "$theme_font_ready" = "1" ]', live)
        self.assertIn("insmod gfxmenu", live)
        self.assertIn("insmod png", live)
        self.assertNotIn(".pf2", live)

    def test_editor_has_enough_width_to_remain_left_anchored(self) -> None:
        config = (THEME / "theme.txt").read_text(encoding="utf-8")
        left = re.search(r'^terminal-left: "(\d+)%"$', config, re.MULTILINE)
        width = re.search(r'^terminal-width: "(\d+)%"$', config, re.MULTILINE)
        self.assertIsNotNone(left)
        self.assertIsNotNone(width)
        self.assertLessEqual(int(left.group(1)), 8)
        # GRUB silently centers a terminal narrower than its 80-column editor.
        self.assertGreaterEqual(int(width.group(1)), 66)
        self.assertLessEqual(int(left.group(1)) + int(width.group(1)), 100)

    def test_activation_is_guarded_and_does_not_change_other_grub_policy(self) -> None:
        config = (PACKAGE / "assets/30-anduinos-hyperfluent.cfg").read_text(
            encoding="utf-8"
        )
        self.assertIn("if [ -f /usr/share/grub/themes/anduinos-hyperfluent/theme.txt ]", config)
        self.assertIn("GRUB_THEME=", config)
        for forbidden in ("GRUB_TIMEOUT", "GRUB_DEFAULT", "GRUB_CMDLINE", "GRUB_TERMINAL"):
            self.assertNotIn(forbidden, config)

    def test_maintainer_scripts_refresh_only_on_install_and_remove(self) -> None:
        for script in (POSTINST, POSTRM):
            subprocess.run(["/bin/sh", "-n", script], check=True)
        for script, action, refresh in (
            (POSTINST, "configure", True),
            (POSTINST, "abort-upgrade", False),
            (POSTRM, "remove", True),
            (POSTRM, "purge", True),
            (POSTRM, "upgrade", False),
            (POSTRM, "failed-upgrade", False),
        ):
            with self.subTest(script=script.name, action=action):
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary)
                    log = directory / "update-grub.log"
                    fake_command(directory, "systemd-detect-virt", "exit 1")
                    fake_command(directory, "ischroot", "exit 1")
                    fake_command(
                        directory,
                        "update-grub",
                        'printf "updated\\n" >> "$GRUB_TEST_LOG"',
                    )
                    env = {
                        **os.environ,
                        "PATH": f"{directory}:/usr/bin:/bin",
                        "GRUB_TEST_LOG": str(log),
                    }
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)
                    self.assertEqual(log.exists(), refresh)

    def test_chroot_does_not_refresh_host_grub(self) -> None:
        for detector in ("systemd-detect-virt", "ischroot"):
            with self.subTest(detector=detector), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                log = directory / "update-grub.log"
                for name in ("systemd-detect-virt", "ischroot"):
                    fake_command(directory, name, f"exit {0 if name == detector else 1}")
                fake_command(directory, "update-grub", f'touch "{log}"')
                env = {**os.environ, "PATH": f"{directory}:/usr/bin:/bin"}
                for script, action in ((POSTINST, "configure"), (POSTRM, "remove")):
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)
                self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
