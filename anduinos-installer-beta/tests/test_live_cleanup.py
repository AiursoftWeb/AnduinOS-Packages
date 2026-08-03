import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.live_cleanup import CleanupLiveSystemStep
from installer_core.model import Filesystem, SourceSpec
from installer_core.steps import InstallContext


class LiveCleanupTests(unittest.TestCase):
    def test_purges_only_manifest_difference_and_installed_self(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            full = root / "filesystem.manifest"
            desktop = root / "filesystem.manifest-desktop"
            full.write_text(
                "bash 1\ncasper 2\nubiquity 3\nshared-package 4\n"
            )
            desktop.write_text("bash 1\nshared-package 4\n")
            plan = replace(
                valid_plan(),
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
            )
            for package in (
                "casper",
                "ubiquity",
                "anduinos-installer-beta",
            ):
                runner.outputs[
                    (
                        "chroot",
                        str(target),
                        "dpkg-query",
                        "--show",
                        "--showformat=${db:Status-Abbrev}",
                        package,
                    )
                ] = ("ii \n", "", 0)
            context = InstallContext(
                plan, lambda _message: None, values={"target": target}
            )
            step = CleanupLiveSystemStep(runner)
            step.preflight(context)
            step.execute(context)

        purge = next(
            command
            for command, _kwargs in runner.commands
            if "purge" in command
        )
        self.assertEqual(
            purge[-3:],
            ("anduinos-installer-beta", "casper", "ubiquity"),
        )
        self.assertNotIn("bash", purge)
        self.assertNotIn("shared-package", purge)

    def test_rejects_manifest_package_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full"
            desktop = root / "desktop"
            full.write_text("valid 1\nbad;command 2\n")
            desktop.write_text("valid 1\n")
            plan = replace(
                valid_plan(),
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
            )
            context = InstallContext(
                plan,
                lambda _message: None,
                values={"target": root / "target"},
            )
            with self.assertRaisesRegex(RuntimeError, "Invalid package"):
                CleanupLiveSystemStep(FakeRunner()).preflight(context)

    def test_rejects_desktop_package_absent_from_full_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full"
            desktop = root / "desktop"
            full.write_text("bash 1\n", encoding="utf-8")
            desktop.write_text("bash 1\ninvented 2\n", encoding="utf-8")
            plan = replace(
                valid_plan(),
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
            )
            context = InstallContext(plan, lambda _message: None)
            with self.assertRaisesRegex(RuntimeError, "absent from the full"):
                CleanupLiveSystemStep(FakeRunner()).preflight(context)

    def test_rejects_live_installer_in_desktop_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full"
            desktop = root / "desktop"
            manifest = "bash 1\nanduinos-installer-beta 2\n"
            full.write_text(manifest, encoding="utf-8")
            desktop.write_text(manifest, encoding="utf-8")
            plan = replace(
                valid_plan(),
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "Live-only packages"):
                CleanupLiveSystemStep(FakeRunner()).preflight(
                    InstallContext(plan, lambda _message: None)
                )

    def test_btrfs_installation_retains_timeback_live_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            full = root / "full"
            desktop = root / "desktop"
            full.write_text(
                "bash 1\nanduinos-timeback-machine 2\n",
                encoding="utf-8",
            )
            desktop.write_text("bash 1\n", encoding="utf-8")
            base = valid_plan()
            plan = replace(
                base,
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
            )
            runner = FakeRunner()
            logs: list[str] = []
            context = InstallContext(plan, logs.append, values={"target": target})
            step = CleanupLiveSystemStep(runner)
            step.preflight(context)
            step.execute(context)

        queried = [
            command[-1]
            for command, _ in runner.commands
            if "dpkg-query" in command
        ]
        self.assertTrue(context.values["timeback_payload_in_live_image"])
        self.assertEqual(context.values["timeback_payload_version"], "2")
        self.assertNotIn("anduinos-timeback-machine", queried)
        combined_logs = "\n".join(logs)
        self.assertIn("payload: present (2)", combined_logs)
        self.assertIn("excluded from the live-package purge set", combined_logs)

    def test_ext4_installation_purges_timeback_live_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            full = root / "full"
            desktop = root / "desktop"
            full.write_text(
                "bash 1\nanduinos-timeback-machine 2\n",
                encoding="utf-8",
            )
            desktop.write_text("bash 1\n", encoding="utf-8")
            base = valid_plan(filesystem=Filesystem.EXT4)
            plan = replace(
                base,
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
            )
            runner = FakeRunner()
            query = (
                "chroot",
                str(target),
                "dpkg-query",
                "--show",
                "--showformat=${db:Status-Abbrev}",
                "anduinos-timeback-machine",
            )
            runner.outputs[query] = ("ii \n", "", 0)
            logs: list[str] = []
            context = InstallContext(plan, logs.append, values={"target": target})
            step = CleanupLiveSystemStep(runner)
            step.preflight(context)
            step.execute(context)

        purge = next(
            command
            for command, _ in runner.commands
            if "purge" in command
        )
        self.assertEqual(purge[-1], "anduinos-timeback-machine")
        combined_logs = "\n".join(logs)
        self.assertIn("included in the live-package purge set", combined_logs)
        self.assertIn("removed from the ext4 target", combined_logs)

    def test_rejects_timeback_in_unconditional_desktop_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full"
            desktop = root / "desktop"
            manifest = "bash 1\nanduinos-timeback-machine 2\n"
            full.write_text(manifest, encoding="utf-8")
            desktop.write_text(manifest, encoding="utf-8")
            base = valid_plan()
            plan = replace(
                base,
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "Conditional live packages",
            ):
                CleanupLiveSystemStep(FakeRunner()).preflight(
                    InstallContext(plan, lambda _message: None)
                )

    def test_chinese_plan_does_not_require_rime_in_desktop_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full"
            desktop = root / "desktop"
            full.write_text("bash 1\nibus-rime 2\n", encoding="utf-8")
            desktop.write_text("bash 1\nibus-rime 2\n", encoding="utf-8")
            base = valid_plan()
            plan = replace(
                base,
                source=SourceSpec(
                    image_path="/unused",
                    manifest_path=str(full),
                    desktop_manifest_path=str(desktop),
                ),
                regional=replace(base.regional, input_method="rime"),
            )
            context = InstallContext(plan, lambda _message: None)
            CleanupLiveSystemStep(FakeRunner()).preflight(context)
            self.assertNotIn(
                "anduinos-rime", context.values["casper_desktop_manifest"]
            )
