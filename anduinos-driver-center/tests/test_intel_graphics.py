"""Pure detection/policy tests: no real Intel GPU or privileged commands."""
from pathlib import Path
from dataclasses import replace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_driver_center import intel_graphics as intel
from anduinos_driver_center import intel_graphics_settings as settings


def completed(command, output="", code=0):
    return subprocess.CompletedProcess(command, code, output, "")


class InventoryTests(unittest.TestCase):
    def test_discovers_intel_without_ubuntu_driver_packages_and_tracks_bound_driver(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pci = root / "sys/bus/pci/devices/0000:00:02.0"
            pci.mkdir(parents=True)
            for name, value in {"vendor": "0x8086", "class": "0x030000", "device": "0x46a6", "modalias": "pci:fixture"}.items():
                (pci / name).write_text(value)
            (pci / "driver").symlink_to("../../drivers/i915")
            panel = pci / "drm/card1/card1-eDP-1"
            panel.mkdir(parents=True)
            (panel / "status").write_text("connected")
            devices = intel.discover(root, lambda command: completed(command, "0000:00:02.0 VGA compatible controller: Intel Iris Xe"))
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0].driver, "i915")
            self.assertTrue(devices[0].internal_panel)
            (pci / "vendor").write_text("0x10de")
            self.assertEqual(intel.discover(root), [])

    def test_log_is_limited_to_selected_gpu_without_entire_journal(self):
        output = "secret login user\ni915 0000:00:02.0: PSR failed\nxe 0000:03:00.0: GPU HANG\n"
        log = intel.graphics_log(output, {"0000:00:02.0"})
        self.assertEqual(log, "i915 0000:00:02.0: PSR failed")
        self.assertEqual(intel.issues_from_log(log), ["psr"])
        self.assertEqual(intel.issues_from_log("i915: GuC firmware loaded successfully"), [])
        self.assertEqual(intel.issues_from_log("xe: GuC firmware load failed"), ["firmware"])

    def test_module_existence_does_not_enable_switching(self):
        device = intel.IntelDevice("0000:00:02.0", "46a6", "Intel", "i915", "pci:fixture", True)
        self.assertFalse(intel.can_switch(device, "xe", ["test-kernel"], lambda cmd: completed(cmd, "pci:*")))
        with patch.object(intel, "VALIDATED_SWITCHES", frozenset({("test-kernel", "46a6", "xe")})):
            self.assertTrue(intel.can_switch(device, "xe", ["test-kernel"], lambda cmd: completed(cmd, "pci:*")))
            self.assertFalse(intel.can_switch(device, "xe", ["test-kernel", "new-kernel"], lambda cmd: completed(cmd, "pci:*")))

    def test_debug_status_reads_only_fixed_files_for_the_correct_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = intel.IntelDevice("0000:00:02.0", "46a6", "Intel", "i915", "pci:fixture", True)
            (root / "sys/bus/pci/devices/0000:00:02.0/drm/card1").mkdir(parents=True)
            debug = root / "sys/kernel/debug/dri/1"
            debug.mkdir(parents=True)
            (debug / "name").write_text("i915 dev=0000:00:02.0")
            (debug / "i915_edp_psr_status").write_text("PSR mode: disabled")
            (debug / "i915_error_state").write_text("private GPU memory")
            evidence = intel.debug_status([device], root)
            self.assertEqual(evidence, {"0000:00:02.0/PSR": "PSR mode: disabled"})
            (debug / "name").write_text("i915 dev=0000:03:00.0")
            self.assertEqual(intel.debug_status([device], root), {})


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / settings.CONFIG
        self.config.parent.mkdir(parents=True)
        self.grub = self.root / "boot/grub/grub.cfg"
        self.grub.parent.mkdir(parents=True)
        self.grub.write_text("original boot configuration")
        (self.root / "boot/vmlinuz-test-kernel").write_text("")
        (self.root / "proc").mkdir()
        (self.root / "proc/cmdline").write_text("quiet splash preempt=full")
        self.device = intel.IntelDevice("0000:00:02.0", "46a6", "Intel", "i915", "pci:fixture", True)
        self.discovery = patch.object(settings, "discover", return_value=[self.device])
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.commands = []
        self.request = {"settings": {"i915.enable_psr": "disabled"}, "switches": {}}

    def runner(self, command):
        self.commands.append(command)
        if command[0] == "modinfo":
            return completed(command, "\n".join(f"{param}:info" for param in intel.PARAMETERS))
        if command[0] == "grub-mkconfig":
            tokens = settings.saved(self.root)
            recovery = "systemd.unit=multi-user.target nomodeset" if tokens else "recovery"
            Path(command[-1]).write_text("menuentry 'normal' {\n linux /vmlinuz-test-kernel quiet preempt=full " + " ".join(tokens)
                                        + "\n}\nmenuentry 'recovery' {\n linux /vmlinuz-test-kernel " + recovery + "\n}\n")
        return completed(command)

    def test_all_controls_roundtrip_for_both_drivers(self):
        for driver in intel.DRIVERS:
            device = intel.IntelDevice(self.device.address, "46a6", "Intel", driver, "pci:fixture", True)
            with patch.object(settings, "discover", return_value=[device]):
                request = {"settings": {f"{driver}.{param}": "disabled" for param in intel.PARAMETERS}, "switches": {}}
                settings.apply(request, self.root, self.runner)
                tokens = settings.saved(self.root)
                self.assertEqual(len(tokens), 4)
                self.assertIn("preempt=full", self.grub.read_text())
                self.assertEqual(settings.parse(settings.render(tokens)), tokens)
                self.assertTrue(settings.status(self.root)["pending"])
                (self.root / "proc/cmdline").write_text("quiet " + " ".join(tokens))
                self.assertFalse(settings.status(self.root)["pending"])
                settings.apply(None, self.root, self.runner)
                self.assertFalse(self.config.exists())
                (self.root / "proc/cmdline").write_text("quiet")

    def test_reset_keeps_user_configuration(self):
        self.config.write_text(settings.render(["i915.enable_psr=0"]))
        foreign = self.root / "etc/default/grub"
        foreign.write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet i915.enable_guc=3"\n')
        settings.apply(None, self.root, self.runner)
        self.assertIn("i915.enable_guc=3", foreign.read_text())
        self.assertFalse(self.config.exists())

    def test_foreign_overrides_block_apply_without_modifying_boot(self):
        foreign = self.root / "etc/default/grub"
        foreign.write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet i915.enable_psr=0"\n')
        with self.assertRaisesRegex(ValueError, "External"):
            settings.apply(self.request, self.root, self.runner)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.grub.read_text(), "original boot configuration")

    def test_grub_failure_restores_existing_settings_and_boot(self):
        before = settings.render(["i915.enable_fbc=0"])
        self.config.write_text(before)
        for failed in ("grub-mkconfig", "grub-script-check"):
            def runner(command):
                return completed(command, "simulated failure", 9) if command[0] == failed else self.runner(command)
            with self.subTest(command=failed), self.assertRaises(RuntimeError):
                settings.apply(self.request, self.root, runner)
            self.assertEqual(self.config.read_text(), before)
            self.assertEqual(self.grub.read_text(), "original boot configuration")

    def test_recovery_must_not_receive_overrides(self):
        def runner(command):
            result = self.runner(command)
            if command[0] == "grub-mkconfig":
                path = Path(command[-1])
                path.write_text(path.read_text().replace(" nomodeset", " nomodeset i915.enable_psr=0"))
            return result
        with self.assertRaisesRegex(RuntimeError, "recovery"):
            settings.apply(self.request, self.root, runner)
        self.assertFalse(self.config.exists())

    def test_recovery_without_friendly_recovery_package_uses_single(self):
        def runner(command):
            result = self.runner(command)
            if command[0] == "grub-mkconfig":
                path = Path(command[-1])
                path.write_text(path.read_text().replace(" recovery", " single nomodeset"))
            return result
        settings.apply(self.request, self.root, runner)
        self.assertEqual(settings.saved(self.root), ["i915.enable_psr=0"])
        settings.apply(None, self.root, runner)
        self.assertIn("single nomodeset", self.grub.read_text())

    def test_missing_parameter_in_any_installed_kernel_blocks_apply(self):
        (self.root / "boot/vmlinuz-old-kernel").write_text("")
        def runner(command):
            if "old-kernel" in command:
                return completed(command, "force_probe:info")
            return self.runner(command)
        with self.assertRaisesRegex(ValueError, "installed kernel"):
            settings.apply(self.request, self.root, runner)

    def test_rejects_injection_and_duplicate_parameters(self):
        for tokens in (["i915.enable_psr=0; reboot"], ["xe.force_probe=*"], ["i915.enable_psr=0", "i915.enable_psr=0"],
                       ["xe.force_probe=46a6", "xe.force_probe=1234"]):
            with self.subTest(tokens=tokens), self.assertRaises(ValueError):
                settings.render(tokens)
        for value in ("0", "off", "$(id)", {}, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.plan({"settings": {"i915.enable_psr": value}, "switches": {}}, self.root, self.runner)

    def test_rejects_foreign_managed_file_and_symlink(self):
        self.config.write_text("echo arbitrary-script\n")
        with self.assertRaises(ValueError):
            settings.apply(None, self.root, self.runner)
        self.config.unlink()
        self.config.symlink_to(self.grub)
        with self.assertRaises(ValueError):
            settings.apply(None, self.root, self.runner)
        self.assertEqual(self.grub.read_text(), "original boot configuration")

    def test_interrupted_transaction_requires_reset(self):
        pending = self.root / "var/lib/anduinos-driver-center/intel-graphics/pending"
        pending.parent.mkdir(parents=True)
        pending.write_text("interrupted")
        with self.assertRaisesRegex(ValueError, "interrupted"):
            settings.apply(self.request, self.root, self.runner)
        settings.apply(None, self.root, self.runner)
        self.assertFalse(pending.exists())

    def test_live_session_cannot_change_installed_boot(self):
        (self.root / "run/initramfs/live").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "Live"):
            settings.apply(self.request, self.root, self.runner)

    def test_unvalidated_switch_never_writes_configuration(self):
        request = {"settings": {}, "switches": {self.device.address: "xe"}}
        with self.assertRaisesRegex(ValueError, "acceptance"):
            settings.apply(request, self.root, self.runner)
        self.assertFalse(self.config.exists())

    def test_switch_parameters_target_device_id_without_global_blacklist(self):
        with patch.object(settings, "can_switch", return_value=True):
            tokens = settings.plan({"settings": {}, "switches": {self.device.address: "xe"}}, self.root, self.runner)
            self.assertEqual(tokens, ["i915.force_probe=!46a6", "xe.force_probe=46a6"])
            with patch.object(settings, "discover", return_value=[self.device, replace(self.device, address="0000:03:00.0")]):
                with self.assertRaisesRegex(ValueError, "multiple devices"):
                    settings.plan({"settings": {}, "switches": {self.device.address: "xe"}}, self.root, self.runner)

    def test_apt_lock_contention_does_not_touch_boot_files(self):
        lock = self.root / "var/lib/dpkg/lock-frontend"
        lock.parent.mkdir(parents=True)
        lock.touch()
        with patch.object(settings.fcntl, "lockf", side_effect=BlockingIOError), self.assertRaisesRegex(RuntimeError, "package operation"):
            settings.apply(self.request, self.root, self.runner)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.grub.read_text(), "original boot configuration")

    def test_snapshots_and_other_operating_systems_do_not_inherit_display_overrides(self):
        def runner(command):
            result = self.runner(command)
            if command[0] == "grub-mkconfig":
                path = Path(command[-1])
                path.write_text("### BEGIN /etc/grub.d/10_linux ###\n" + path.read_text()
                                + "### END /etc/grub.d/10_linux ###\n"
                                + "menuentry 'snapshot' {\n linux /snapshot/vmlinuz anduinos.btrfs_snapshots_manager=123\n}\n")
            return result
        settings.apply(self.request, self.root, runner)
        snapshot = self.grub.read_text().split("menuentry 'snapshot'", 1)[1]
        self.assertNotIn("i915.enable_psr", snapshot)


if __name__ == "__main__":
    unittest.main()
