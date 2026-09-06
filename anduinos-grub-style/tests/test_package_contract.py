#!/usr/bin/env python3

import os
import stat
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
PROJECT_FILE = PROJECT / "anduinos-grub-style.aosproj"
CORE_PROJECT_FILE = PROJECT.parent / "anduinos-core-system/anduinos-core-system.aosproj"
CONFIG = PROJECT / "assets/20-anduinos-style.cfg"
POSTINST = PROJECT / "scripts/postinst.sh"
POSTRM = PROJECT / "scripts/postrm.sh"

CONFIG_TEXT = """# Prefer a lower graphics mode while keeping GRUB's trusted default Unicode font.
GRUB_GFXMODE="1440x900,1280x800,1280x720,1024x768,auto"
# Let Linux and Plymouth select their own platform-appropriate video mode.
GRUB_GFXPAYLOAD_LINUX="auto"
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


class GrubStylePackageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = ET.parse(PROJECT_FILE).getroot()

    def test_package_metadata(self) -> None:
        self.assertEqual(
            self.project.findtext(".//PackageName"), "anduinos-grub-style"
        )
        self.assertEqual(
            self.project.findtext(".//PackageVersion"),
            "2.0.2-1+$(SuiteShortName)",
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
        self.assertEqual(self.project.findall(".//IncludeFile"), [])
        config_files = {
            item.get("Include"): item.get("Target")
            for item in self.project.findall(".//ConfFile")
        }
        self.assertEqual(
            config_files,
            {
                "assets/20-anduinos-style.cfg": (
                    "/etc/default/grub.d/20-anduinos-style.cfg"
                )
            },
        )

    def test_package_boundary_stays_small(self) -> None:
        for field in ("Conflicts", "Replaces", "Provides"):
            self.assertIsNone(self.project.find(f".//{field}"))
        self.assertEqual(list(PROJECT.rglob("*.pf2")), [])
        self.assertFalse((PROJECT / "generate-font.sh").exists())
        lifecycle = POSTINST.read_text() + POSTRM.read_text()
        self.assertNotIn("update-initramfs", lifecycle)
        self.assertNotIn("/boot/efi", lifecycle)

    def test_grub_drop_in_is_exact_and_override_friendly(self) -> None:
        self.assertEqual(CONFIG.read_text(encoding="utf-8"), CONFIG_TEXT)
        self.assertTrue(CONFIG.name.startswith("20-"))
        self.assertNotIn("GRUB_FONT", CONFIG_TEXT)
        self.assertNotIn("GRUB_CMDLINE", CONFIG_TEXT)
        self.assertIn('GRUB_GFXPAYLOAD_LINUX="auto"', CONFIG_TEXT)

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
                        / "etc/default/grub.d/20-anduinos-style.cfg"
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
        dependencies = [
            item
            for item in core.findall(".//Dependency")
            if item.get("Include") == "anduinos-grub-style"
        ]
        self.assertEqual(len(dependencies), 1)
        self.assertIsNone(dependencies[0].get("Condition"))
        self.assertEqual(
            core.findall(".//Recommend[@Include='anduinos-grub-style']"), []
        )


if __name__ == "__main__":
    unittest.main()
