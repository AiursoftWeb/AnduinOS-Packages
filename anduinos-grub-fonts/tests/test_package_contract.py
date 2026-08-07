#!/usr/bin/env python3

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
PROJECT_FILE = PROJECT / "anduinos-grub-fonts.aosproj"
CORE_PROJECT_FILE = PROJECT.parent / "anduinos-core-system/anduinos-core-system.aosproj"
FONT = PROJECT / "assets/anduinos-unicode-28.pf2"
CONFIG = PROJECT / "assets/20-anduinos-font.cfg"
COPYRIGHT = PROJECT / "assets/copyright"
GENERATOR = PROJECT / "generate-font.sh"
POSTINST = PROJECT / "scripts/postinst.sh"
POSTRM = PROJECT / "scripts/postrm.sh"

FONT_SHA256 = "112ceb12fb241561cb7e710b324536e9f6cb2d86d02683cf0b23bc11de9acea4"
CONFIG_TEXT = """# Keep the GRUB menu readable on HiDPI displays.
GRUB_FONT="/usr/share/grub/anduinos/anduinos-unicode-28.pf2"
GRUB_GFXMODE="1440x900,1280x800,1280x720,1024x768,auto"
"""


def write_fake_command(directory: Path, name: str, body: str) -> Path:
    command = directory / name
    command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    return command


def install_fake_chroot_detectors(
    directory: Path, systemd_result: int = 1, ischroot_result: int = 1
) -> None:
    write_fake_command(directory, "systemd-detect-virt", f"exit {systemd_result}")
    write_fake_command(directory, "ischroot", f"exit {ischroot_result}")


class GrubFontsPackageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ET.parse(PROJECT_FILE).getroot()

    def test_package_metadata(self) -> None:
        self.assertEqual(
            self.project.findtext(".//PackageName"), "anduinos-grub-fonts"
        )
        self.assertEqual(
            self.project.findtext(".//PackageVersion"),
            "2.0.0-1+$(SuiteShortName)",
        )
        self.assertEqual(self.project.findtext(".//Section"), "admin")
        self.assertEqual(
            self.project.findtext(".//LicenseType"), "GPL-2.0-or-later"
        )
        self.assertEqual(
            self.project.findtext(".//TargetSuites"),
            "noble-addon resolute-addon",
        )
        self.assertEqual(self.project.findtext(".//TargetArchitectures"), "all")
        self.assertEqual(self.project.findtext(".//Component"), "main")
        self.assertEqual(
            self.project.findtext(".//SuiteShortNameMap"),
            "noble-addon=noble resolute-addon=resolute",
        )

    def test_only_runtime_dependency_is_grub2_common(self) -> None:
        dependencies = {
            item.get("Include") for item in self.project.findall(".//Dependency")
        }
        self.assertEqual(dependencies, {"grub2-common"})
        self.assertNotIn("fonts-unifont", dependencies)
        source = self.project.find(".//DependencyCheckSource")
        self.assertIsNotNone(source)
        self.assertEqual(source.get("Url"), "https://mirror.aiursoft.com/ubuntu")

    def test_exact_assets_are_packaged(self) -> None:
        included = {
            item.get("Include"): item.get("Target")
            for item in self.project.findall(".//IncludeFile")
        }
        self.assertEqual(
            included,
            {
                "assets/anduinos-unicode-28.pf2": (
                    "/usr/share/grub/anduinos/anduinos-unicode-28.pf2"
                ),
                "assets/copyright": (
                    "/usr/share/doc/anduinos-grub-fonts/copyright"
                ),
            },
        )
        config_files = {
            item.get("Include"): item.get("Target")
            for item in self.project.findall(".//ConfFile")
        }
        self.assertEqual(
            config_files,
            {
                "assets/20-anduinos-font.cfg": (
                    "/etc/default/grub.d/20-anduinos-font.cfg"
                )
            },
        )

    def test_pf2_is_the_expected_28_pixel_unifont(self) -> None:
        data = FONT.read_bytes()
        self.assertEqual(len(data), 5_423_135)
        self.assertTrue(data.startswith(b"FILE\x00\x00\x00\x04PFF2"))
        self.assertIn(b"Unifont Regular 28\x00", data[:128])
        self.assertEqual(hashlib.sha256(data).hexdigest(), FONT_SHA256)

    def test_grub_drop_in_is_exact_and_override_friendly(self) -> None:
        self.assertEqual(CONFIG.read_text(encoding="utf-8"), CONFIG_TEXT)
        self.assertTrue(CONFIG.name.startswith("20-"))
        self.assertNotIn("GRUB_CMDLINE", CONFIG_TEXT)

    def test_source_and_license_provenance_are_recorded(self) -> None:
        generator = GENERATOR.read_text(encoding="utf-8")
        copyright_text = COPYRIGHT.read_text(encoding="utf-8")
        self.assertIn('EXPECTED_UNIFONT_VERSION="1:16.0.04-1build1"', generator)
        self.assertIn('EXPECTED_GRUB_VERSION="2.14-2ubuntu2.1"', generator)
        self.assertIn(
            'EXPECTED_SOURCE_SHA256="0e3981ab552231b5a2a870f2b61741903'
            'a4bf25c23ef5aeb05fdced1b3c7af4d"',
            generator,
        )
        self.assertIn(f'EXPECTED_SHA256="{FONT_SHA256}"', generator)
        self.assertIn("GNU Unifont 16.0.04", copyright_text)
        self.assertIn("License: GPL-2+", copyright_text)

    def test_lifecycle_scripts_and_contract_test_are_wired(self) -> None:
        self.assertEqual(
            self.project.find(".//PostInstallScript").get("Include"),
            "scripts/postinst.sh",
        )
        self.assertEqual(
            self.project.find(".//PostRemoveScript").get("Include"),
            "scripts/postrm.sh",
        )
        self.assertEqual(
            self.project.find(".//PrebuildCommand").get("Run"),
            "python3 tests/test_package_contract.py",
        )

    def test_maintainer_scripts_have_valid_posix_shell_syntax(self) -> None:
        for script in (POSTINST, POSTRM):
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("set -eu\n"))
                self.assertNotIn("#!/bin/sh", text)
                subprocess.run(["/bin/sh", "-n", script], check=True)

    def test_maintainer_script_action_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            log = fake_bin / "calls.log"
            write_fake_command(
                fake_bin,
                "update-grub",
                'printf "%s\\n" update-grub >> "$UPDATE_GRUB_LOG"',
            )
            install_fake_chroot_detectors(fake_bin)
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "UPDATE_GRUB_LOG": str(log),
            }

            cases = (
                (POSTINST, "configure", 1, False),
                (POSTINST, "abort-upgrade", 0, False),
                (POSTRM, "remove", 1, True),
                (POSTRM, "purge", 1, True),
                (POSTRM, "upgrade", 0, False),
            )
            for script, action, expected_calls, removes_config in cases:
                with self.subTest(script=script.name, action=action):
                    config = (
                        test_root
                        / "etc/default/grub.d/20-anduinos-font.cfg"
                    )
                    config.parent.mkdir(parents=True, exist_ok=True)
                    config.write_text(CONFIG_TEXT, encoding="utf-8")
                    log.unlink(missing_ok=True)
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)
                    calls = (
                        log.read_text(encoding="utf-8").splitlines()
                        if log.exists()
                        else []
                    )
                    self.assertEqual(calls, ["update-grub"] * expected_calls)
                    self.assertEqual(config.exists(), not removes_config)

    def test_chroot_defers_update_grub(self) -> None:
        for detector, systemd_result, ischroot_result in (
            ("systemd-detect-virt", 0, 1),
            ("ischroot", 1, 0),
        ):
            with self.subTest(detector=detector), tempfile.TemporaryDirectory() as temp_dir:
                fake_bin = Path(temp_dir) / "bin"
                fake_bin.mkdir()
                log = fake_bin / "calls.log"
                write_fake_command(
                    fake_bin,
                    "update-grub",
                    'printf "%s\\n" update-grub >> "$UPDATE_GRUB_LOG"',
                )
                install_fake_chroot_detectors(
                    fake_bin,
                    systemd_result=systemd_result,
                    ischroot_result=ischroot_result,
                )
                env = {
                    **os.environ,
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "UPDATE_GRUB_LOG": str(log),
                }
                result = subprocess.run(
                    ["/bin/sh", POSTINST, "configure"],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertFalse(log.exists())
                self.assertIn("deferring GRUB configuration refresh", result.stdout)

    def test_update_grub_failure_is_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            write_fake_command(fake_bin, "update-grub", "exit 23")
            install_fake_chroot_detectors(fake_bin)
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            for script, action in ((POSTINST, "configure"), (POSTRM, "remove")):
                with self.subTest(script=script.name):
                    result = subprocess.run(["/bin/sh", script, action], env=env)
                    self.assertEqual(result.returncode, 23)

    def test_core_system_owns_the_boot_readability_dependency(self) -> None:
        core = ET.parse(CORE_PROJECT_FILE).getroot()
        self.assertEqual(
            core.findtext(".//PackageVersion"),
            "2.0.0-4+$(SuiteShortName)",
        )
        dependencies = [
            item
            for item in core.findall(".//Dependency")
            if item.get("Include") == "anduinos-grub-fonts"
        ]
        self.assertEqual(len(dependencies), 1)
        self.assertIsNone(dependencies[0].get("Condition"))
        self.assertEqual(
            core.findall(".//Recommend[@Include='anduinos-grub-fonts']"), []
        )


if __name__ == "__main__":
    unittest.main()
