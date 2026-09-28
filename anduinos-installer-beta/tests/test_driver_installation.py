"""Regression coverage for platform-kernel changes during driver installation."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.command import CommandError
from installer_core.software import InstallThirdPartyDriversStep, verify_driver_boot_payload
from installer_core.steps import InstallContext


class DriverRunner(FakeRunner):
    """Model generic -> platform kernel -> matching modules, without host writes."""

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.installs = 0
        self.keep_changing = False
        self.introduce_kernel = True
        self.module_kernel = "7.0.0-generic"
        self.missing_module = ""
        self.packages = "nvidia-driver-595-open:arm64\tii \n"
        self.final_guard_error = False

    def run(self, command, **kwargs):
        command = tuple(command)
        if command[2:4] == ("ubuntu-drivers", "install"):
            self.installs += 1
            if self.introduce_kernel and self.installs == 1:
                (self.target / "boot/vmlinuz-7.0.0-platform").write_text("kernel")
            if self.installs == 2:
                self.module_kernel = "7.0.0-platform"
                if self.keep_changing:
                    (self.target / "boot/vmlinuz-7.0.0-other").write_text("kernel")
        if command[2:4] == ("dpkg-query", "-W"):
            self.outputs[command] = (self.packages, "", 0)
        if command[2] == "modinfo":
            self.outputs[command] = (
                self.module_kernel + " SMP preempt mod_unload aarch64\n", "",
                1 if command[-1] == self.missing_module else 0,
            )
        if command[-1] == "--verify-default" and self.final_guard_error:
            self.commands.append((command, kwargs))
            raise CommandError("missing or empty initrd")
        return super().run(command, **kwargs)


class DriverInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name)
        for relative in ("usr/bin/ubuntu-drivers", "usr/libexec/anduinos-dracut-verify",
                         "boot/vmlinuz-7.0.0-generic"):
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
        self.context = InstallContext(
            valid_plan(install_third_party_drivers=True), lambda _message: None,
            {"target": self.target, "chroot_environment_ready": True},
        )
        self.runner = DriverRunner(self.target)
        self.step = InstallThirdPartyDriversStep(self.runner)
        self.grub = (
            "menuentry 'AnduinOS' {\n"
            " linux /@root/boot/vmlinuz-7.0.0-platform root=UUID=test\n"
            " initrd /@root/boot/initrd.img-7.0.0-platform\n}\n"
            "menuentry 'Generic fallback' {\n"
            " linux /boot/vmlinuz-7.0.0-generic\n}\n"
        )

    def test_platform_kernel_introduced_by_driver_transaction_is_resolved_again(self):
        self.step.execute(self.context)
        self.step.verify(self.context)
        verify_driver_boot_payload(self.context, self.runner, self.grub)
        self.assertEqual(self.runner.installs, 2)
        commands = [command for command, _ in self.runner.commands]
        for module in ("nvidia", "nvidia_modeset", "nvidia_drm", "nvidia_uvm"):
            self.assertIn(
                ("chroot", str(self.target), "modinfo", "-k", "7.0.0-platform",
                 "-F", "vermagic", module), commands,
            )
        self.assertFalse(any("uname" in command for command in commands))
        self.assertTrue(all(command[:2] == ("chroot", str(self.target)) for command in commands))
        self.assertTrue(self.context.values["third_party_drivers_installed"])

    def test_stable_kernel_set_does_not_repeat_installation(self):
        self.runner.introduce_kernel = False
        self.step.execute(self.context)
        self.assertEqual(self.runner.installs, 1)
        self.assertTrue(self.context.values["third_party_drivers_installed"])

    def test_kernel_changes_are_bounded_to_two_transactions(self):
        self.runner.keep_changing = True
        with self.assertRaisesRegex(CommandError, "kept changing"):
            self.step.execute(self.context)
        self.assertEqual(self.runner.installs, 2)
        self.assertFalse(self.context.values["third_party_drivers_installed"])

    def test_zero_outer_exit_does_not_hide_unconfigured_packages(self):
        self.runner.outputs[("chroot", str(self.target), "dpkg", "--audit")] = (
            "nvidia-kernel-common is unpacked but not configured\n", "", 0,
        )
        with self.assertRaisesRegex(CommandError, "inconsistent package state"):
            self.step.execute(self.context)
        self.assertEqual(self.runner.installs, 1)
        self.assertFalse(self.context.values["third_party_drivers_installed"])

    def test_zero_outer_exit_does_not_hide_broken_dependencies_or_failed_audit(self):
        for command in (("apt-get", "check"), ("dpkg", "--audit")):
            with self.subTest(command=command):
                runner = DriverRunner(self.target)
                runner.outputs[("chroot", str(self.target), *command)] = ("", "failed", 100)
                with self.assertRaisesRegex(CommandError, "inconsistent package state"):
                    InstallThirdPartyDriversStep(runner).execute(self.context)
                self.assertFalse(self.context.values["third_party_drivers_installed"])

    def test_second_transaction_is_also_audited(self):
        original_run = self.runner.run

        def run(command, **kwargs):
            if tuple(command)[2:4] == ("dpkg", "--audit") and self.runner.installs == 2:
                return subprocess.CompletedProcess(command, 0, "unconfigured modules", "")
            return original_run(command, **kwargs)

        self.runner.run = run
        with self.assertRaisesRegex(CommandError, "inconsistent package state"):
            self.step.execute(self.context)
        self.assertEqual(self.runner.installs, 2)
        self.assertFalse(self.context.values["third_party_drivers_installed"])

    def test_final_guard_failure_is_not_swallowed(self):
        self.step.execute(self.context)
        self.runner.final_guard_error = True
        with self.assertRaisesRegex(CommandError, "missing or empty initrd"):
            verify_driver_boot_payload(self.context, self.runner, self.grub)

    def test_failed_second_resolution_is_fatal_even_with_clean_dpkg_state(self):
        original_run = self.runner.run

        def run(command, **kwargs):
            result = original_run(command, **kwargs)
            if tuple(command)[2:4] == ("ubuntu-drivers", "install") and self.runner.installs == 2:
                return subprocess.CompletedProcess(command, 100, "", "download failed")
            return result

        self.runner.run = run
        with self.assertRaisesRegex(CommandError, "changed target kernels"):
            self.step.execute(self.context)
        self.assertEqual(self.runner.installs, 2)
        self.assertFalse(self.context.values["third_party_drivers_installed"])

    def test_generic_modules_cannot_satisfy_the_platform_boot_kernel(self):
        self.step.execute(self.context)
        self.runner.module_kernel = "7.0.0-generic"
        with self.assertRaisesRegex(CommandError, "incompatible.*7.0.0-platform"):
            verify_driver_boot_payload(self.context, self.runner, self.grub)

    def test_each_nvidia_module_must_exist(self):
        self.step.execute(self.context)
        for module in ("nvidia", "nvidia_modeset", "nvidia_drm", "nvidia_uvm"):
            with self.subTest(module=module):
                self.runner.missing_module = module
                with self.assertRaisesRegex(CommandError, module + " is missing"):
                    verify_driver_boot_payload(self.context, self.runner, self.grub)

    def test_empty_modinfo_output_does_not_raise_index_error(self):
        self.step.execute(self.context)
        self.runner.module_kernel = ""
        original_run = self.runner.run

        def run(command, **kwargs):
            if "modinfo" in command:
                return subprocess.CompletedProcess(command, 0, "", "")
            return original_run(command, **kwargs)

        self.runner.run = run
        with self.assertRaisesRegex(CommandError, "missing or incompatible"):
            verify_driver_boot_payload(self.context, self.runner, self.grub)

    def test_non_nvidia_system_does_not_require_nvidia_modules(self):
        self.step.execute(self.context)
        self.runner.packages = "bcmwl-kernel-source\tii \nnvidia-driver-old\trc \n"
        verify_driver_boot_payload(self.context, self.runner, self.grub)
        self.assertFalse(any("modinfo" in command for command, _ in self.runner.commands))

    def test_missing_or_unknown_boot_kernel_is_fatal(self):
        self.step.execute(self.context)
        for config in ("", " linux /boot/vmlinuz-nonexistent\n"):
            with self.subTest(config=config):
                with self.assertRaisesRegex(CommandError, "identify the target kernel"):
                    verify_driver_boot_payload(self.context, self.runner, config)

    def test_unselected_or_offline_drivers_do_not_add_final_checks(self):
        verify_driver_boot_payload(self.context, self.runner, self.grub)
        self.assertEqual(self.runner.commands, [])


if __name__ == "__main__":
    unittest.main()
