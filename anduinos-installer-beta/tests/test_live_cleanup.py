import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.live_cleanup import (
    LIVE_ONLY_PACKAGES,
    RemoveLivePackagesStep,
)
from installer_core.model import Filesystem
from installer_core.steps import InstallContext
from installer_core.snapshots_manager import SNAPSHOTS_MANAGER_PACKAGE


EXPECTED_LIVE_ONLY_PACKAGES = (
    "casper",
    "discover",
    "laptop-detect",
    "os-prober",
    "gparted",
    "anduinos-installer-beta",
    "anduinos-live-settings",
)


def _query(target: Path, package: str) -> tuple[str, ...]:
    return (
        "chroot",
        str(target),
        "dpkg-query",
        "--show",
        "--showformat=${db:Status-Abbrev}",
        package,
    )


def _context(
    root: Path,
    filesystem: Filesystem = Filesystem.BTRFS,
) -> InstallContext:
    return InstallContext(
        valid_plan(filesystem=filesystem),
        lambda _message: None,
        values={"target": root, "chroot_environment_ready": True},
    )


class RemoveLivePackagesTests(unittest.TestCase):
    def test_install_plan_source_has_no_ubiquity_manifest_contract(self):
        plan = valid_plan()
        payload = plan.to_dict()
        self.assertEqual(
            payload["source"],
            {"image_path": "/cdrom/casper/filesystem.squashfs"},
        )
        payload["source"]["desktop_manifest_path"] = "/legacy"
        with self.assertRaisesRegex(
            ValueError, "Unknown field in source: desktop_manifest_path"
        ):
            type(plan).from_dict(payload)

    def test_policy_is_explicit_and_snapshots_manager_is_not_unconditional(self):
        self.assertEqual(LIVE_ONLY_PACKAGES, EXPECTED_LIVE_ONLY_PACKAGES)
        self.assertNotIn(SNAPSHOTS_MANAGER_PACKAGE, LIVE_ONLY_PACKAGES)

    def test_btrfs_purges_installed_live_packages_and_retains_snapshots_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            for package in ("casper", "anduinos-installer-beta"):
                runner.outputs[_query(target, package)] = ("ii \n", "", 0)
            context = _context(target)
            step = RemoveLivePackagesStep(runner)
            step.preflight(context)
            step.execute(context)

        purge = next(
            command for command, _kwargs in runner.commands if "purge" in command
        )
        self.assertEqual(
            purge[-2:], ("casper", "anduinos-installer-beta")
        )
        queried = {
            command[-1]
            for command, _kwargs in runner.commands
            if "dpkg-query" in command
        }
        self.assertEqual(queried, set(EXPECTED_LIVE_ONLY_PACKAGES))
        self.assertNotIn(SNAPSHOTS_MANAGER_PACKAGE, queried)

    def test_ext4_adds_snapshots_manager_to_the_purge_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            runner.outputs[_query(target, SNAPSHOTS_MANAGER_PACKAGE)] = ("ii \n", "", 0)
            context = _context(target, Filesystem.EXT4)
            step = RemoveLivePackagesStep(runner)
            step.preflight(context)
            step.execute(context)

        purge = next(
            command for command, _kwargs in runner.commands if "purge" in command
        )
        self.assertEqual(purge[-1], SNAPSHOTS_MANAGER_PACKAGE)
        self.assertEqual(
            context.values["live_package_candidates"][-1], SNAPSHOTS_MANAGER_PACKAGE
        )

    def test_missing_packages_are_a_successful_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner()
            context = _context(Path(directory))
            step = RemoveLivePackagesStep(runner)
            step.preflight(context)
            step.execute(context)

        self.assertEqual(context.values["live_packages_removed"], ())
        self.assertFalse(
            any("purge" in command for command, _kwargs in runner.commands)
        )

    def test_verify_rejects_any_candidate_that_remains_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            context = _context(target)
            context.values["live_package_candidates"] = LIVE_ONLY_PACKAGES
            runner.outputs[_query(target, "gparted")] = ("ii \n", "", 0)
            with self.assertRaisesRegex(
                RuntimeError, "Live-only packages remain installed: gparted"
            ):
                RemoveLivePackagesStep(runner).verify(context)

    def test_execute_requires_the_prepared_target_chroot(self):
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            values={"target": Path("/target")},
        )
        with self.assertRaisesRegex(RuntimeError, "chroot environment"):
            RemoveLivePackagesStep(FakeRunner()).execute(context)


if __name__ == "__main__":
    unittest.main()
