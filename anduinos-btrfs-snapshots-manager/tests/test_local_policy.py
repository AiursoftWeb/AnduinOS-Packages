#!/usr/bin/env python3
"""Exercise lifecycle scripts with package-owned paths redirected to fixtures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("etc", "var", "run", "boot", "snapshots", "libexec", "sbin", "bin", "modules", "defaults"):
            (self.root / name).mkdir()
        self.config = self.root / "etc/anduinos-btrfs-snapshots-manager"
        self.config.mkdir()
        self.admin = self.root / "etc/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"
        self.admin.parent.mkdir(parents=True)
        self.admin.write_text("administrator override\n")
        self.transient = self.root / "run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service"
        self.transient.parent.mkdir(parents=True)
        self.transient.write_text("old recovery unit\n")
        self.snapshot = self.root / "snapshots/anduinos-btrfs-snapshots-manager/deployments/user-data"
        self.snapshot.parent.mkdir(parents=True)
        self.snapshot.write_bytes(b"irreplaceable snapshot")
        for name in ("apt-snapshots.toml", "automation.toml"):
            (self.root / "defaults" / name).write_bytes((ROOT / "assets" / name).read_bytes())
        self.log = self.root / "run/test-commands"
        self.program(self.root / "libexec/anduinos-dracut-verify", """
printf 'verify %s\\n' "$*" >> "$TEST_COMMAND_LOG"
[ "${TEST_FAIL:-}" != "$1" ] || exit 31
""")
        self.program(self.root / "bin/dracut", """
printf 'dracut %s\\n' "$*" >> "$TEST_COMMAND_LOG"
[ "${TEST_FAIL:-}" != dracut ] || exit 31
""")
        self.program(self.root / "sbin/update-grub", "printf 'update-grub\\n' >> \"$TEST_COMMAND_LOG\"\n")
        self.program(self.root / "bin/grub-editenv", "printf 'grub-editenv %s\\n' \"$*\" >> \"$TEST_COMMAND_LOG\"\n")
        self.program(self.root / "bin/systemd-detect-virt", 'exit "${TEST_CHROOT_EXIT:-1}"\n')
        self.program(self.root / "bin/mountpoint", "exit 1\n")
        for name in ("systemctl", "systemd-tmpfiles", "dbus-send"):
            self.program(self.root / "bin" / name, f"printf '{name} %s\\n' \"$*\" >> \"$TEST_COMMAND_LOG\"\n")

    @staticmethod
    def program(path, body):
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)

    def script_source(self, name):
        source = (ROOT / "scripts" / name).read_text()
        replacements = {
            "/usr/share/anduinos-btrfs-snapshots-manager/defaults": self.root / "defaults",
            "/usr/libexec/anduinos-btrfs-snapshots-manager/no-os-prober": self.root / "libexec/no-os-prober",
            "/usr/libexec/anduinos-dracut-verify": self.root / "libexec/anduinos-dracut-verify",
            "/usr/lib/tmpfiles.d/anduinos-btrfs-snapshots-manager.conf": self.root / "defaults/tmpfiles.conf",
            "/usr/bin/systemd-detect-virt": self.root / "bin/systemd-detect-virt",
            "/usr/bin/grub-editenv": self.root / "bin/grub-editenv",
            "/usr/sbin/update-grub": self.root / "sbin/update-grub",
            "/etc/anduinos-btrfs-snapshots-manager": self.root / "etc/anduinos-btrfs-snapshots-manager",
            "/var/lib/anduinos-btrfs-snapshots-manager": self.root / "var/lib/anduinos-btrfs-snapshots-manager",
            "/run/systemd/system": self.root / "run/systemd/system",
            "/run/anduinos-btrfs-snapshots-manager-grub.": self.root / "run/anduinos-btrfs-snapshots-manager-grub.",
            "/lib/modules": self.root / "modules",
            "/boot/efi": self.root / "boot/efi",
            "/.snapshots": self.root / "snapshots",
        }
        for original, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            source = source.replace(original, str(replacement))
        guarded_source = source
        for replacement in replacements.values():
            guarded_source = guarded_source.replace(str(replacement), "<test-fixture>")
        # Refuse to run while any known package-owned host path remains outside
        # the explicitly redirected fixture names.
        for original in replacements:
            self.assertNotIn(original, guarded_source)
        executable_source = "\n".join(
            line for line in guarded_source.splitlines()
            if not line.lstrip().startswith("#")
        )
        for host_prefix in ("/etc/", "/var/", "/run/", "/boot/", "/.snapshots", "/lib/modules"):
            self.assertNotIn(host_prefix, executable_source)
        return source

    def run_script(self, name, action, *, failure="", chroot=False):
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            ["/bin/sh", "-s", "--", action],
            input=self.script_source(name), capture_output=True, text=True, timeout=15,
            env={**os.environ, "PATH": f"{self.root / 'bin'}:/usr/bin:/bin",
                 "TEST_COMMAND_LOG": str(self.log), "TEST_FAIL": failure,
                 "TEST_CHROOT_EXIT": "0" if chroot else "1"},
        )
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result, calls

    def test_rebuild_failure_stops_configuration_and_removal(self):
        for name, action in (("postinst.sh", "configure"), ("postrm.sh", "purge")):
            with self.subTest(script=name):
                result, calls = self.run_script(name, action, failure="--rebuild")
                self.assertEqual(result.returncode, 31, result.stderr)
                self.assertEqual(calls, ["verify --rebuild"])
                self.assertEqual(self.transient.read_text(), "old recovery unit\n")
                self.assertEqual(self.snapshot.read_bytes(), b"irreplaceable snapshot")

    def test_standalone_dracut_failure_is_not_hidden(self):
        (self.root / "libexec/anduinos-dracut-verify").unlink()
        for name, action in (("postinst.sh", "configure"), ("postrm.sh", "remove")):
            with self.subTest(script=name):
                result, calls = self.run_script(name, action, failure="dracut")
                self.assertEqual(result.returncode, 31, result.stderr)
                self.assertEqual(calls, ["dracut --force --regenerate-all"])

    def test_configuration_preserves_custom_policy_and_removes_only_transient_override(self):
        policy = self.config / "apt-snapshots.toml"
        policy.write_text("# administrator policy\nsnapshot_before = false\n")
        result, calls = self.run_script("postinst.sh", "configure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(policy.read_text(), "# administrator policy\nsnapshot_before = false\n")
        self.assertEqual((self.config / "automation.toml").read_bytes(), (self.root / "defaults/automation.toml").read_bytes())
        self.assertFalse(self.transient.exists())
        self.assertEqual(self.admin.read_text(), "administrator override\n")
        self.assertEqual(self.snapshot.read_bytes(), b"irreplaceable snapshot")
        self.assertIn("verify --update-grub", calls)

    def test_configuration_rejects_symlink_without_overwriting_destination(self):
        destination = self.root / "etc/private-data"
        destination.write_text("preserve me")
        (self.config / "apt-snapshots.toml").symlink_to("/etc/private-data")
        result, calls = self.run_script("postinst.sh", "configure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe configuration symlink", result.stderr)
        self.assertEqual(destination.read_text(), "preserve me")
        self.assertNotIn("verify --update-grub", calls)

    def test_chroot_never_refreshes_host_grub(self):
        for name, action in (("postinst.sh", "configure"), ("postrm.sh", "remove")):
            with self.subTest(script=name):
                result, calls = self.run_script(name, action, chroot=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("verify --update-grub", calls)
                self.assertNotIn("update-grub", calls)

    def test_purge_preserves_snapshot_data_and_administrator_overrides(self):
        (self.config / "apt-snapshots.toml").write_text("old settings")
        (self.config / "automation.toml").write_text("old settings")
        result, _ = self.run_script("postrm.sh", "purge")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.snapshot.read_bytes(), b"irreplaceable snapshot")
        self.assertEqual(self.admin.read_text(), "administrator override\n")


if __name__ == "__main__":
    unittest.main()
