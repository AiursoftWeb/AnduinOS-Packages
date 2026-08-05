#!/usr/bin/env python3
import os
import stat
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
PROJECT_FILE = PROJECT / "anduinos-live-settings.aosproj"
INSTALLER_PROJECT = PROJECT.parent / "anduinos-installer-beta/anduinos-installer-beta.aosproj"
HOOK = PROJECT / "assets/14anduinos-timezone"
POSTINST = PROJECT / "scripts/postinst.sh"
POSTRM = PROJECT / "scripts/postrm.sh"


class LiveSettingsPackageContractTests(unittest.TestCase):
    def setUp(self):
        self.project = ET.parse(PROJECT_FILE).getroot()

    def test_package_identity_dependencies_and_hook(self):
        self.assertEqual(
            self.project.findtext(".//PackageVersion"),
            "2.0.1-1+$(SuiteShortName)",
        )
        dependencies = {
            item.get("Include") for item in self.project.findall(".//Dependency")
        }
        self.assertEqual(dependencies, {"casper", "initramfs-tools"})
        included = self.project.find(
            ".//IncludeFile[@Include='assets/14anduinos-timezone']"
        )
        self.assertIsNotNone(included)
        self.assertEqual(
            included.get("Target"),
            "/usr/share/initramfs-tools/scripts/casper-bottom/14anduinos-timezone",
        )
        self.assertEqual(included.get("Mode"), "755")
        self.assertFalse((PROJECT / "assets/30anduinos-timezone").exists())

    def test_installer_declares_the_live_bridge_dependency(self):
        installer = ET.parse(INSTALLER_PROJECT).getroot()
        dependencies = {
            item.get("Include") for item in installer.findall(".//Dependency")
        }
        self.assertIn("anduinos-live-settings", dependencies)

    def test_valid_and_hostile_timezone_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            support = root / "casper-functions"
            cmdline = root / "cmdline"
            (target / "usr/share/zoneinfo/Asia").mkdir(parents=True)
            (target / "usr/share/zoneinfo/Asia/Shanghai").touch()
            support.write_text(
                "log_begin_msg() { :; }\nlog_end_msg() { :; }\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "CASPER_FUNCTIONS": str(support),
                "CASPER_CMDLINE_FILE": str(cmdline),
                "CASPER_TARGET_ROOT": str(target),
            }

            cmdline.write_text(
                "boot=casper timezone=Asia/Shanghai quiet splash\n",
                encoding="utf-8",
            )
            subprocess.run(["/bin/sh", HOOK], env=env, check=True)
            self.assertEqual(
                (target / "etc/timezone").read_text(encoding="utf-8"),
                "Asia/Shanghai\n",
            )
            self.assertEqual(
                os.readlink(target / "etc/localtime"),
                "/usr/share/zoneinfo/Asia/Shanghai",
            )

            for value in ("../../etc/passwd", "/etc/passwd", "Asia/Bad;Name"):
                with self.subTest(value=value):
                    (target / "etc/timezone").unlink(missing_ok=True)
                    (target / "etc/localtime").unlink(missing_ok=True)
                    cmdline.write_text(
                        f"boot=casper timezone={value}\n", encoding="utf-8"
                    )
                    subprocess.run(["/bin/sh", HOOK], env=env, check=True)
                    self.assertFalse((target / "etc/timezone").exists())
                    self.assertFalse((target / "etc/localtime").exists())

    def test_maintainer_scripts_rebuild_all_initrds_on_lifecycle_changes(self):
        for script in (POSTINST, POSTRM):
            subprocess.run(["/bin/sh", "-n", script], check=True)
            self.assertTrue(script.read_text(encoding="utf-8").startswith("set -eu\n"))

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_bin = Path(temp_dir)
            log = fake_bin / "calls.log"
            update = fake_bin / "update-initramfs"
            update.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$UPDATE_LOG\"\n",
                encoding="utf-8",
            )
            update.chmod(update.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "UPDATE_LOG": str(log),
            }
            cases = (
                (POSTINST, "configure", True),
                (POSTINST, "abort-upgrade", False),
                (POSTRM, "remove", True),
                (POSTRM, "purge", True),
                (POSTRM, "upgrade", False),
            )
            for script, action, expected in cases:
                with self.subTest(script=script.name, action=action):
                    log.unlink(missing_ok=True)
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)
                    lines = (
                        log.read_text(encoding="utf-8").splitlines()
                        if log.exists()
                        else []
                    )
                    self.assertEqual(lines, ["-u -k all"] if expected else [])


if __name__ == "__main__":
    unittest.main()
