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
DESKTOP_PROJECT_FILE = PROJECT.parent / "anduinos-desktop/anduinos-desktop.aosproj"
CONFIG = PROJECT / "assets/99-anduinos-desktop.cfg"
LEGACY_CONFIG = PROJECT / "assets/50-anduinos-desktop.cfg"
POSTINST = PROJECT / "scripts/postinst.sh"
POSTRM = PROJECT / "scripts/postrm.sh"


class KernelParametersPackageContractTests(unittest.TestCase):
    def setUp(self):
        self.project_text = PROJECT_FILE.read_text(encoding="utf-8")
        self.project = ET.fromstring(self.project_text)

    def test_package_targets_only_resolute_as_architecture_all(self):
        self.assertEqual(
            self.project.findtext(".//PackageVersion"),
            "2.0.0-2+$(SuiteShortName)",
        )
        self.assertEqual(self.project.findtext(".//TargetSuites"), "resolute-addon")
        self.assertEqual(self.project.findtext(".//TargetArchitectures"), "all")
        self.assertEqual(self.project.findtext(".//Component"), "main")
        self.assertEqual(self.project.findtext(".//Section"), "admin")
        self.assertEqual(
            self.project.findtext(".//SuiteShortNameMap"),
            "resolute-addon=resolute",
        )

    def test_dependencies_conflict_and_resolute_check_source_are_declared(self):
        dependencies = {
            item.get("Include") for item in self.project.findall(".//Dependency")
        }
        self.assertEqual(dependencies, {"grub2-common", "linux-generic"})
        self.assertNotIn("kernel-supports-lowlatency-bootargs", dependencies)
        self.assertEqual(self.project.findtext(".//Conflicts"), "lowlatency-kernel")
        source = self.project.find(".//DependencyCheckSource")
        self.assertIsNotNone(source)
        self.assertEqual(source.get("Url"), "https://mirror.aiursoft.com/ubuntu")
        self.assertEqual(source.get("SuiteMap"), "resolute-addon=resolute")

    def test_exact_grub_drop_in_is_packaged(self):
        included = self.project.find(
            ".//IncludeFile[@Include='assets/99-anduinos-desktop.cfg']"
        )
        self.assertIsNotNone(included)
        self.assertEqual(
            included.get("Target"),
            "/etc/default/grub.d/99-anduinos-desktop.cfg",
        )
        self.assertEqual(self.project.findall(".//ConfFile"), [])
        self.assertEqual(
            CONFIG.read_text(encoding="utf-8"),
            'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT preempt=full"\n',
        )
        self.assertFalse(LEGACY_CONFIG.exists())

    def test_desktop_recommends_the_policy_only_on_resolute(self):
        desktop = ET.parse(DESKTOP_PROJECT_FILE).getroot()
        self.assertEqual(
            desktop.findtext(".//PackageVersion"),
            "2.0.1-5+$(SuiteShortName)",
        )
        matching_dependencies = desktop.findall(
            ".//Dependency[@Include='anduinos-kernel-parameters']"
        )
        matching_recommendations = desktop.findall(
            ".//Recommend[@Include='anduinos-kernel-parameters']"
        )
        self.assertEqual(matching_dependencies, [])
        self.assertEqual(len(matching_recommendations), 1)
        self.assertEqual(
            matching_recommendations[0].get("Condition"),
            "'$(Suite)' == 'resolute-addon'",
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
                self.assertTrue(text.startswith("set -eu\n"))
                self.assertNotIn("#!/bin/sh", text)
                self.assertNotIn('/etc/default/grub"', text)
                self.assertNotIn("sed", text)

    def test_maintainer_scripts_have_valid_shell_syntax(self):
        for script in (POSTINST, POSTRM):
            with self.subTest(script=script.name):
                subprocess.run(["/bin/sh", "-n", script], check=True)

    def test_maintainer_script_action_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            log = fake_bin / "calls.log"
            update_grub = fake_bin / "update-grub"
            update_grub.write_text(
                '#!/bin/sh\nprintf "%s\\n" update-grub >> "$UPDATE_GRUB_LOG"\n',
                encoding="utf-8",
            )
            update_grub.chmod(update_grub.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "UPDATE_GRUB_LOG": str(log),
            }

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
                    policy_file = (
                        test_root / "etc/default/grub.d/99-anduinos-desktop.cfg"
                    )
                    legacy_policy_file = (
                        test_root / "etc/default/grub.d/50-anduinos-desktop.cfg"
                    )
                    policy_file.parent.mkdir(parents=True, exist_ok=True)
                    policy_file.write_text("test policy\n", encoding="utf-8")
                    legacy_policy_file.write_text("legacy policy\n", encoding="utf-8")
                    dpkg_removes_policy = script == POSTRM and action in {
                        "remove",
                        "purge",
                    }
                    if dpkg_removes_policy:
                        policy_file.unlink()
                    log.unlink(missing_ok=True)
                    reboot_required = test_root / "run/reboot-required"
                    reboot_packages = test_root / "run/reboot-required.pkgs"
                    reboot_required.unlink(missing_ok=True)
                    reboot_packages.unlink(missing_ok=True)
                    subprocess.run(["/bin/sh", script, action], env=env, check=True)
                    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
                    self.assertEqual(calls, ["update-grub"] * expected_calls)
                    self.assertEqual(reboot_required.exists(), bool(expected_calls))
                    packages = (
                        reboot_packages.read_text(encoding="utf-8").splitlines()
                        if reboot_packages.exists()
                        else []
                    )
                    self.assertEqual(
                        packages,
                        ["anduinos-kernel-parameters"] if expected_calls else [],
                    )
                    self.assertEqual(policy_file.exists(), not dpkg_removes_policy)
                    should_remove_legacy_policy = (
                        script == POSTINST and action == "configure"
                    ) or dpkg_removes_policy
                    self.assertEqual(
                        legacy_policy_file.exists(), not should_remove_legacy_policy
                    )

    def test_reboot_package_marker_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            update_grub = fake_bin / "update-grub"
            update_grub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            update_grub.chmod(update_grub.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            subprocess.run(["/bin/sh", POSTINST, "configure"], env=env, check=True)
            subprocess.run(["/bin/sh", POSTINST, "configure"], env=env, check=True)

            packages = (test_root / "run/reboot-required.pkgs").read_text(
                encoding="utf-8"
            )
            self.assertEqual(packages, "anduinos-kernel-parameters\n")

    def test_update_grub_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            update_grub = fake_bin / "update-grub"
            update_grub.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
            update_grub.chmod(update_grub.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            for script, action in ((POSTINST, "configure"), (POSTRM, "remove")):
                with self.subTest(script=script.name):
                    result = subprocess.run(["/bin/sh", script, action], env=env)
                    self.assertEqual(result.returncode, 23)

    def test_missing_update_grub_is_a_safe_no_op(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir) / "root"
            empty_bin = Path(temp_dir) / "bin"
            empty_bin.mkdir()
            env = {
                **os.environ,
                "DPKG_ROOT": str(test_root),
                "PATH": f"{empty_bin}:/usr/bin:/bin",
            }
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
