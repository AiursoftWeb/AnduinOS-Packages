import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.live_cleanup import (
    LIVE_ONLY_PACKAGES,
    PERSISTENT_TARGET_PACKAGES,
    REQUIRED_BOOT_PACKAGES,
    RemoveLivePackagesStep,
    VMWARE_GUEST_PACKAGES,
)
from installer_core.model import Architecture, Filesystem
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


DETECT_VIRTUALIZATION = ("systemd-detect-virt", "--vm")


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
        self.assertEqual(PERSISTENT_TARGET_PACKAGES, ("openssh-server",))
        self.assertEqual(
            REQUIRED_BOOT_PACKAGES[Architecture.AMD64],
            (
                "anduinos-core-system",
                "grub-common",
                "grub2-common",
                "grub-pc-bin",
                "grub-efi-amd64-bin",
                "grub-efi-amd64-signed",
                "shim-signed",
            ),
        )
        self.assertEqual(
            REQUIRED_BOOT_PACKAGES[Architecture.ARM64],
            (
                "anduinos-core-system",
                "grub-common",
                "grub2-common",
                "grub-efi-arm64-bin",
                "grub-efi-arm64-signed",
                "shim-signed",
            ),
        )
        self.assertEqual(
            VMWARE_GUEST_PACKAGES,
            (
                "open-vm-tools-desktop",
                "open-vm-tools",
                "xserver-xorg-video-vmware",
            ),
        )

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
        self.assertEqual(
            queried,
            set(EXPECTED_LIVE_ONLY_PACKAGES) | {"openssh-server"},
        )
        self.assertNotIn(SNAPSHOTS_MANAGER_PACKAGE, queried)

    def test_marks_live_composed_openssh_as_a_persistent_target_package(self):
        messages = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            runner.outputs[_query(target, "openssh-server")] = (
                "ii \n",
                "",
                0,
            )
            runner.outputs[_query(target, "anduinos-live-settings")] = (
                "ii \n",
                "",
                0,
            )
            context = InstallContext(
                valid_plan(),
                messages.append,
                values={
                    "target": target,
                    "chroot_environment_ready": True,
                },
            )
            RemoveLivePackagesStep(runner).execute(context)

        commands = [command for command, _kwargs in runner.commands]
        mark = (
            "chroot",
            str(target),
            "apt-mark",
            "manual",
            "openssh-server",
        )
        purge_index = next(
            index
            for index, command in enumerate(commands)
            if "purge" in command and "autoremove" not in command
        )
        self.assertLess(commands.index(mark), purge_index)
        self.assertEqual(
            context.values["persistent_target_packages"],
            ("openssh-server",),
        )
        self.assertIn(
            "Retaining installed-system packages: openssh-server",
            messages,
        )

    def test_vmware_target_retains_all_vmware_guest_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            runner.outputs[DETECT_VIRTUALIZATION] = ("vmware\n", "", 0)
            runner.outputs[_query(target, "casper")] = ("ii \n", "", 0)
            context = _context(target)
            RemoveLivePackagesStep(runner).execute(context)

        self.assertEqual(context.values["install_virtualization"], "vmware")
        self.assertTrue(
            set(VMWARE_GUEST_PACKAGES).isdisjoint(
                context.values["live_package_candidates"]
            )
        )
        queried = {
            command[-1]
            for command, _kwargs in runner.commands
            if "dpkg-query" in command
        }
        self.assertTrue(set(VMWARE_GUEST_PACKAGES).isdisjoint(queried))

    def test_physical_target_purges_vmware_family_and_orphans(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            runner.outputs[DETECT_VIRTUALIZATION] = ("none\n", "", 1)
            for package in VMWARE_GUEST_PACKAGES:
                runner.outputs[_query(target, package)] = ("ii \n", "", 0)
            context = _context(target)
            RemoveLivePackagesStep(runner).execute(context)

        self.assertEqual(context.values["install_virtualization"], "physical")
        purge = next(
            command
            for command, _kwargs in runner.commands
            if "purge" in command and "autoremove" not in command
        )
        self.assertEqual(purge[-3:], VMWARE_GUEST_PACKAGES)
        self.assertIn(
            (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "--yes",
                "autoremove",
                "--purge",
            ),
            [command for command, _kwargs in runner.commands],
        )

    def test_other_hypervisor_purges_vmware_guest_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            runner.outputs[DETECT_VIRTUALIZATION] = ("kvm\n", "", 0)
            context = _context(target)
            RemoveLivePackagesStep(runner).execute(context)

        self.assertEqual(context.values["install_virtualization"], "kvm")
        self.assertTrue(
            set(VMWARE_GUEST_PACKAGES).issubset(
                context.values["live_package_candidates"]
            )
        )

    def test_inconclusive_detection_retains_vmware_guest_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            runner.outputs[DETECT_VIRTUALIZATION] = ("", "probe failed", 2)
            context = _context(target)
            RemoveLivePackagesStep(runner).execute(context)

        self.assertIsNone(context.values["install_virtualization"])
        self.assertTrue(
            set(VMWARE_GUEST_PACKAGES).isdisjoint(
                context.values["live_package_candidates"]
            )
        )

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

    def test_verify_rejects_autoremove_of_a_persistent_target_package(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            context = _context(target)
            context.values["live_package_candidates"] = LIVE_ONLY_PACKAGES
            context.values["persistent_target_packages"] = (
                "openssh-server",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "Installed-system packages were removed: openssh-server",
            ):
                RemoveLivePackagesStep(runner).verify(context)

    def test_verify_rejects_missing_declarative_boot_package(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            context = _context(target)
            context.values["live_package_candidates"] = ()
            context.values["persistent_target_packages"] = ()
            for package in REQUIRED_BOOT_PACKAGES[Architecture.AMD64]:
                if package != "grub-pc-bin":
                    runner.outputs[_query(target, package)] = (
                        "ii \n",
                        "",
                        0,
                    )

            with self.assertRaisesRegex(
                RuntimeError,
                "boot packages are missing after cleanup: grub-pc-bin",
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
