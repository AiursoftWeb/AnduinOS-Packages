import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.live_cleanup import CleanupLiveSystemStep
from installer_core.model import SourceSpec
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

    def test_chinese_plan_requires_rime_payload_in_desktop_manifest(self):
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
            with self.assertRaisesRegex(
                RuntimeError, "anduinos-rime, libglib2.0-bin"
            ):
                CleanupLiveSystemStep(FakeRunner()).preflight(
                    InstallContext(plan, lambda _message: None)
                )

    def test_chinese_plan_accepts_complete_retained_rime_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full"
            desktop = root / "desktop"
            manifest = (
                "bash 1\nanduinos-rime 2\nibus-rime 3\nlibglib2.0-bin 4\n"
            )
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
                regional=replace(base.regional, input_method="rime"),
            )
            context = InstallContext(plan, lambda _message: None)
            CleanupLiveSystemStep(FakeRunner()).preflight(context)
            self.assertIn(
                "anduinos-rime",
                context.values["casper_desktop_manifest"],
            )
