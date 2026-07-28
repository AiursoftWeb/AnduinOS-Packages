import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.execution_steps import CopySystemStep, UnmountTargetStep
from installer_core.model import SourceSpec
from installer_core.steps import InstallContext


class CopySystemTests(unittest.TestCase):
    def test_preflight_requires_existing_source(self):
        plan = replace(
            valid_plan(),
            source=SourceSpec(image_path="/definitely/missing.squashfs"),
        )
        with self.assertRaisesRegex(RuntimeError, "System image not found"):
            CopySystemStep(FakeRunner()).preflight(
                InstallContext(plan, lambda _message: None)
            )

    def test_execute_and_verify_target(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "filesystem.squashfs"
            source.touch()
            target = root / "target"
            (target / "etc").mkdir(parents=True)
            (target / "etc/os-release").touch()
            (target / "usr").mkdir()
            (target / "var").mkdir()
            plan = replace(
                valid_plan(), source=SourceSpec(image_path=str(source))
            )
            context = InstallContext(
                plan, lambda _message: None, values={"target": target}
            )
            step = CopySystemStep(runner)
            step.preflight(context)
            step.execute(context)
            step.verify(context)
        self.assertEqual(runner.commands[-1][0][0], "unsquashfs")


class UnmountTargetTests(unittest.TestCase):
    def test_unmounts_children_first_and_clears_state(self):
        runner = FakeRunner()
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            values={
                "target": Path("/target-test"),
                "target_efi_mounted": True,
                "target_root_mounted": True,
            },
        )
        step = UnmountTargetStep(runner)
        step.execute(context)
        step.verify(context)
        self.assertEqual(
            [item[0] for item in runner.commands],
            [
                ("umount", "/target-test/boot/efi"),
                ("umount", "/target-test"),
            ],
        )

