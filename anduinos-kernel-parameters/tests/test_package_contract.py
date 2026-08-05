#!/usr/bin/env python3
import os
import stat
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent
PROJECT_FILE = PROJECT / "anduinos-kernel-parameters.aosproj"
CONFIG = PROJECT / "assets/50-anduinos-desktop.cfg"
POSTINST = PROJECT / "scripts/postinst.sh"
POSTRM = PROJECT / "scripts/postrm.sh"


class KernelParametersPackageContractTests(unittest.TestCase):
    def setUp(self):
        self.project_text = PROJECT_FILE.read_text(encoding="utf-8")
        self.project = ET.fromstring(self.project_text)

    def test_package_targets_only_resolute_as_architecture_all(self):
        self.assertEqual(
            self.project.findtext(".//PackageVersion"),
            "2.0.0-1+$(SuiteShortName)",
        )
        self.assertEqual(self.project.findtext(".//TargetSuites"), "resolute-addon")
        self.assertEqual(self.project.findtext(".//TargetArchitectures"), "all")
        self.assertEqual(self.project.findtext(".//Component"), "main")
        self.assertEqual(self.project.findtext(".//Section"), "admin")
        self.assertEqual(
            self.project.findtext(".//SuiteShortNameMap"),
            "resolute-addon=resolute",
        )

    def test_dependency_and_resolute_check_source_are_declared(self):
        dependencies = {
            item.get("Include") for item in self.project.findall(".//Dependency")
        }
        self.assertEqual(dependencies, {"grub2-common"})
        source = self.project.find(".//DependencyCheckSource")
        self.assertIsNotNone(source)
        self.assertEqual(source.get("Url"), "https://mirror.aiursoft.com/ubuntu")
        self.assertEqual(source.get("SuiteMap"), "resolute-addon=resolute")

    def test_exact_grub_drop_in_is_packaged(self):
        included = self.project.find(
            ".//ConfFile[@Include='assets/50-anduinos-desktop.cfg']"
        )
        self.assertIsNotNone(included)
        self.assertEqual(
            included.get("Target"),
            "/etc/default/grub.d/50-anduinos-desktop.cfg",
        )
        self.assertEqual(
            CONFIG.read_text(encoding="utf-8"),
            'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT preempt=full"\n',
        )

    def test_lifecycle_scripts_and_contract_test_are_wired_into_the_package(self):
        postinst = self.project.find(".//PostInstallScript")
        postrm = self.project.find(".//PostRemoveScript")
        prebuild = self.project.find(".//PrebuildCommand")
        self.assertIsNotNone(postinst)
        self.assertIsNotNone(postrm)
        self.assertIsNotNone(prebuild)
        self.assertEqual(postinst.get("Include"), "scripts/postinst.sh")
        self.assertEqual(postrm.get("Include"), "scripts/postrm.sh")
        self.assertEqual(prebuild.get("Run"), "python3 tests/test_package_contract.py")

    def test_scripts_do_not_rewrite_the_main_grub_defaults(self):
        for script in (POSTINST, POSTRM):
            text = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertIn("set -e", text)
                self.assertNotIn("/etc/default/grub", text)
                self.assertNotIn("sed", text)

    def test_maintainer_scripts_have_valid_shell_syntax(self):
        for script in (POSTINST, POSTRM):
            with self.subTest(script=script.name):
                subprocess.run(["/bin/sh", "-n", script], check=True)

    def test_maintainer_script_action_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_bin = Path(temp_dir)
            log = fake_bin / "calls.log"
            update_grub = fake_bin / "update-grub"
            update_grub.write_text(
                '#!/bin/sh\nprintf "%s\\n" update-grub >> "$UPDATE_GRUB_LOG"\n',
                encoding="utf-8",
            )
            update_grub.chmod(update_grub.stat().st_mode | stat.S_IXUSR)
            env = {**os.environ, "PATH": str(fake_bin), "UPDATE_GRUB_LOG": str(log)}

            cases = (
                (POSTINST, "configure", 1),
                (POSTINST, "abort-upgrade", 0),
                (POSTRM, "remove", 1),
                (POSTRM, "purge", 1),
                (POSTRM, "upgrade", 0),
                (POSTRM, "failed-upgrade", 0),
            )
            for script, action, expected_calls in cases:
                with self.subTest(script=script.name, action=action):
                    log.unlink(missing_ok=True)
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)
                    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
                    self.assertEqual(calls, ["update-grub"] * expected_calls)

    def test_update_grub_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_bin = Path(temp_dir)
            update_grub = fake_bin / "update-grub"
            update_grub.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
            update_grub.chmod(update_grub.stat().st_mode | stat.S_IXUSR)
            env = {**os.environ, "PATH": str(fake_bin)}

            for script, action in ((POSTINST, "configure"), (POSTRM, "remove")):
                with self.subTest(script=script.name):
                    result = subprocess.run(["/bin/sh", script, action], env=env)
                    self.assertEqual(result.returncode, 23)

    def test_missing_update_grub_is_a_safe_no_op(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {**os.environ, "PATH": temp_dir}
            for script, action in ((POSTINST, "configure"), (POSTRM, "purge")):
                with self.subTest(script=script.name):
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)

    def test_repeated_generation_starts_with_one_parameter(self):
        command = (
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"; '
            '. "$1"; printf "%s\\n" "$GRUB_CMDLINE_LINUX_DEFAULT"'
        )
        generated = [
            subprocess.check_output(
                ["/bin/sh", "-c", command, "sh", CONFIG], text=True
            ).strip()
            for _ in range(2)
        ]
        self.assertEqual(generated, ["quiet splash preempt=full"] * 2)
        self.assertTrue(all(value.count("preempt=full") == 1 for value in generated))


if __name__ == "__main__":
    unittest.main()
