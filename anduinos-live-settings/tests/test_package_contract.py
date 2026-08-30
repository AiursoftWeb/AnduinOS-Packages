#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
PROJECT_FILE = PROJECT / "anduinos-live-settings.aosproj"
INSTALLER_PROJECT = PROJECT.parent / "anduinos-installer-beta/anduinos-installer-beta.aosproj"
SETUP = PROJECT / "assets/anduinos-live-session-setup"
SERVICE = PROJECT / "assets/anduinos-live-session.service"
GRUB_DROP_INS = {
    PROJECT / "assets/grub-initrd-fallback-live.conf": (
        "/usr/lib/systemd/system/"
        "grub-initrd-fallback.service.d/10-anduinos-live.conf"
    ),
    PROJECT / "assets/grub2-common-live.conf": (
        "/usr/lib/systemd/system/"
        "grub2-common.service.d/10-anduinos-live.conf"
    ),
}


class LiveSettingsPackageContractTests(unittest.TestCase):
    def setUp(self):
        self.project = ET.parse(PROJECT_FILE).getroot()

    def test_package_identity_dependencies_and_live_policy(self):
        self.assertEqual(
            self.project.findtext(".//PackageName"),
            "anduinos-live-settings",
        )
        dependencies = {
            item.get("Include") for item in self.project.findall(".//Dependency")
        }
        self.assertEqual(
            dependencies,
            {
                "anduinos-live-layers",
                "adduser",
                "locales",
                "xkb-data",
                "sudo",
                "openssh-server",
            },
        )
        project_text = PROJECT_FILE.read_text(encoding="utf-8")
        self.assertNotIn("casper", project_text.lower())
        self.assertNotIn("initramfs-tools", project_text)

        unit = self.project.find(
            ".//SystemdUnit[@Include='assets/anduinos-live-session.service']"
        )
        self.assertIsNotNone(unit)
        self.assertEqual(unit.get("AutoEnable"), "true")
        self.assertIn(
            "ConditionPathExists=/run/anduinos-live/environment",
            SERVICE.read_text(encoding="utf-8"),
        )

        for source, target in GRUB_DROP_INS.items():
            with self.subTest(source=source.name):
                included = self.project.find(
                    f".//IncludeFile[@Include='assets/{source.name}']"
                )
                self.assertIsNotNone(included)
                self.assertEqual(included.get("Target"), target)
                self.assertEqual(included.get("Mode"), "644")
                self.assertEqual(
                    source.read_text(encoding="utf-8"),
                    "[Unit]\nConditionPathExists=!/run/anduinos-live/environment\n",
                )

    def test_installer_declares_the_live_bridge_dependency(self):
        installer = ET.parse(INSTALLER_PROJECT).getroot()
        dependencies = {
            item.get("Include") for item in installer.findall(".//Dependency")
        }
        self.assertIn("anduinos-live-settings", dependencies)

    def test_valid_and_hostile_regional_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cmdline = root / "cmdline"
            (root / "run/anduinos-live").mkdir(parents=True)
            (root / "run/anduinos-live/environment").write_text(
                "ANDUINOS_LIVE=1\n", encoding="utf-8"
            )
            for timezone in ("Asia/Shanghai", "Etc/UTC"):
                zone = root / "usr/share/zoneinfo" / timezone
                zone.parent.mkdir(parents=True, exist_ok=True)
                zone.touch()
            symbols = root / "usr/share/X11/xkb/symbols"
            symbols.mkdir(parents=True)
            for layout in ("us", "fr"):
                (symbols / layout).touch()
            env = {
                **os.environ,
                "ANDUINOS_LIVE_ROOT": str(root),
                "ANDUINOS_LIVE_CMDLINE_FILE": str(cmdline),
                "ANDUINOS_LIVE_TEST_MODE": "1",
            }

            cmdline.write_text(
                "rd.anduinos.live=1 locale=zh_CN.UTF-8 "
                "timezone=Asia/Shanghai rd.anduinos.keyboard=fr "
                "hostname=anduinos quiet\n",
                encoding="utf-8",
            )
            subprocess.run(["/bin/sh", SETUP], env=env, check=True)
            self.assertEqual(
                (root / "etc/timezone").read_text(encoding="utf-8"),
                "Asia/Shanghai\n",
            )
            self.assertEqual(
                os.readlink(root / "etc/localtime"),
                "/usr/share/zoneinfo/Asia/Shanghai",
            )
            self.assertEqual(
                (root / "etc/default/locale").read_text(encoding="utf-8"),
                'LANG="zh_CN.UTF-8"\n',
            )
            self.assertEqual(
                (root / "etc/default/keyboard").read_text(encoding="utf-8"),
                'XKBMODEL="pc105"\n'
                'XKBLAYOUT="fr"\n'
                'XKBVARIANT=""\n'
                'XKBOPTIONS=""\n'
                'BACKSPACE="guess"\n',
            )
            self.assertIn(
                "ANDUINOS_LIVE_KEYBOARD=fr\n",
                (root / "run/anduinos-live/environment").read_text(
                    encoding="utf-8"
                ),
            )

            for value in ("../../etc/passwd", "/etc/passwd", "Asia/Bad;Name"):
                with self.subTest(value=value):
                    cmdline.write_text(
                        f"rd.anduinos.live=1 timezone={value}\n", encoding="utf-8"
                    )
                    subprocess.run(["/bin/sh", SETUP], env=env, check=True)
                    self.assertEqual(
                        (root / "etc/timezone").read_text(encoding="utf-8"),
                        "Etc/UTC\n",
                    )

            for value in ("missing", "../../fr", "fr;touch", "-option"):
                with self.subTest(keyboard=value):
                    cmdline.write_text(
                        f"rd.anduinos.live=1 rd.anduinos.keyboard={value}\n",
                        encoding="utf-8",
                    )
                    subprocess.run(["/bin/sh", SETUP], env=env, check=True)
                    self.assertIn(
                        'XKBLAYOUT="us"\n',
                        (root / "etc/default/keyboard").read_text(
                            encoding="utf-8"
                        ),
                    )

    def test_setup_is_valid_posix_shell_and_has_no_legacy_generator_calls(self):
        subprocess.run(["/bin/sh", "-n", SETUP], check=True)
        setup = SETUP.read_text(encoding="utf-8")
        self.assertIn("useradd --create-home --uid 1000", setup)
        self.assertIn("AutomaticLoginEnable=true", setup)
        self.assertIn('XKBLAYOUT="$live_keyboard"', setup)
        self.assertNotIn("localectl", setup)
        self.assertLess(
            setup.index('XKBLAYOUT="$live_keyboard"'),
            setup.index("useradd --create-home --uid 1000"),
        )
        self.assertNotIn("update-initramfs", setup)
        self.assertNotIn("casper", setup.lower())


if __name__ == "__main__":
    unittest.main()
