"""Recovery readiness must reflect actual disks, cached versions and GRUB output."""
import contextlib
import io
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from anduinos_driver_center import recovery


class RecoveryRunner:
    def __init__(self, *, installed="2.0.5-6", candidate="2.0.5-6", comparison=0,
                 blocks=None, disk_error=False, probe=None):
        self.installed, self.candidate, self.comparison = installed, candidate, comparison
        self.blocks = blocks if blocks is not None else [
            dict(name="/dev/vda4", type="part", fstype="ext4", label="AOS_RECOVERY", uuid="ours")]
        self.disk_error, self.probe = disk_error, probe
        self.commands = []

    def run(self, command, timeout=10):
        self.commands.append(command)
        code, output = 0, ""
        if command[0] == "dpkg-query":
            code = 0 if self.installed else 1
            output = (self.installed if command[2] == "-f=${Version}" else "ii ") if self.installed else ""
        elif command[0] == "apt-cache":
            output = "Candidate: " + (self.candidate or "(none)")
        elif command[0] == "dpkg":
            code = self.comparison
        elif command[0] == "lsblk":
            code = 1 if self.disk_error else 0
            output = json.dumps({"blockdevices": self.blocks})
        elif command[0] == "pkexec":
            code = 1 if self.probe is None else 0
            output = json.dumps({"grub_entry": self.probe})
        else:
            raise AssertionError(f"Unexpected command: {command}")
        return subprocess.CompletedProcess(command, code, output, "")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.binding = Path(self.directory.name, "volume.json")
        self.grub = Path(self.directory.name, "grub.cfg")
        self.grub.write_text("menuentry 'AnduinOS Recovery Environment' --id anduinos-recovery-env {\n}\n")

    def scan(self, runner):
        state = recovery.recovery_state(runner, binding_path=self.binding, grub_path=self.grub)
        self.assertTrue(all(command[0] in {"dpkg-query", "apt-cache", "dpkg", "lsblk", "pkexec"}
                            for command in runner.commands))
        self.assertNotIn("update", [argument for command in runner.commands for argument in command])
        return state

    def test_prepared_partition_does_not_imply_package_or_menu_installed(self):
        self.grub.write_text("# anduinos-recovery-env\nmenuentry 'Ubuntu recovery mode' {\n}\n")
        state = self.scan(RecoveryRunner(installed=None))
        self.assertTrue(state.partition)
        self.assertFalse(state.package.installed)
        self.assertFalse(state.up_to_date)
        self.assertFalse(state.grub_entry)

    def test_cached_update_equal_and_locally_newer_versions(self):
        for comparison, expected in ((0, True), (1, False), (2, None)):
            with self.subTest(comparison=comparison):
                state = self.scan(RecoveryRunner(comparison=comparison))
                self.assertIs(state.up_to_date, expected)
                self.assertTrue(state.grub_entry)
        self.assertIsNone(self.scan(RecoveryRunner(candidate=None)).up_to_date)

    def test_only_one_matching_ext4_partition_counts_as_present(self):
        block = dict(name="/dev/vda4", type="part", fstype="ext4", label="AOS_RECOVERY", uuid="ours")
        for blocks, expected in (([], False), ([block], True), ([block, block], False),
                                 ([dict(block, fstype="btrfs")], False),
                                 ([dict(block, type="disk")], False)):
            with self.subTest(blocks=blocks):
                self.assertIs(self.scan(RecoveryRunner(blocks=blocks)).partition, expected)
        self.assertIsNone(self.scan(RecoveryRunner(disk_error=True)).partition)

    def test_binding_selects_its_volume_and_does_not_fall_back_to_another_label(self):
        self.binding.write_text(json.dumps({"volume_uuid": "foreign"}))
        self.assertFalse(self.scan(RecoveryRunner()).partition)
        self.binding.write_text(json.dumps({"volume_uuid": "ours"}))
        self.assertTrue(self.scan(RecoveryRunner()).partition)
        self.binding.write_text("invalid")
        self.assertIsNone(self.scan(RecoveryRunner()).partition)

    def test_menu_checks_generated_entry_id_instead_of_comments_or_generator(self):
        for text, expected in (
            ("# menuentry 'Recovery' --id anduinos-recovery-env {", False),
            ("menuentry 'Other recovery' --id other {", False),
            ("menuentry 'Recovery' --id=anduinos-recovery-env {", True),
            ('menuentry "Recovery" --id "anduinos-recovery-env" {', True),
        ):
            with self.subTest(text=text):
                self.grub.write_text(text)
                self.assertIs(recovery.grub_entry_present(self.grub), expected)
        self.grub.unlink()
        self.assertFalse(recovery.grub_entry_present(self.grub))

    def test_permission_denial_uses_fixed_probe_and_preserves_unknown_on_failure(self):
        for probe in (True, False, None, "true"):
            with self.subTest(probe=probe), patch.object(recovery, "grub_entry_present", return_value=None):
                runner = RecoveryRunner(probe=probe)
                state = self.scan(runner)
                self.assertIs(state.grub_entry, probe if type(probe) is bool else None)
                self.assertIn(["pkexec", "--disable-internal-agent", recovery.PROBE], runner.commands)

    def test_probe_rejects_arguments_and_unprivileged_calls_and_emits_only_boolean(self):
        path = Path(__file__).resolve().parents[1] / "scripts/recovery-probe"
        loader = SourceFileLoader("recovery_probe", str(path))
        probe = types.ModuleType(loader.name)
        loader.exec_module(probe)
        with patch.object(probe.os, "geteuid", return_value=1000):
            self.assertEqual(probe.main([]), 77)
        with patch.object(probe.os, "geteuid", return_value=0):
            self.assertEqual(probe.main(["/etc/shadow"]), 64)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(probe, "grub_entry_present", return_value=True):
                self.assertEqual(probe.main([]), 0)
            self.assertEqual(json.loads(output.getvalue()), {"grub_entry": True})
