#!/usr/bin/env python3
"""Exercise source lifecycle scripts; requires bubblewrap and user namespaces."""
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
printf 'verify %s\\n' "$*" >> /run/test-commands
[ "${TEST_FAIL:-}" != "$1" ] || exit 31
""")
        self.program(self.root / "bin/dracut", """
printf 'dracut %s\\n' "$*" >> /run/test-commands
[ "${TEST_FAIL:-}" != dracut ] || exit 31
""")
        self.program(self.root / "sbin/update-grub", "printf 'update-grub\\n' >> /run/test-commands\n")
        self.program(self.root / "bin/systemd-detect-virt", 'exit "${TEST_CHROOT_EXIT:-1}"\n')
        self.program(self.root / "bin/mountpoint", "exit 1\n")
        for name in ("systemctl", "systemd-tmpfiles", "dbus-send"):
            self.program(self.root / "bin" / name, f"printf '{name} %s\\n' \"$*\" >> /run/test-commands\n")

    @staticmethod
    def program(path, body):
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)

    def run_script(self, name, action, *, failure="", chroot=False):
        self.log.unlink(missing_ok=True)
        # Only /usr is borrowed read-only. Boot state, snapshots, configuration,
        # runtime directories and command substitutes are private test fixtures.
        command = [
            "bwrap", "--unshare-all", "--die-with-parent",
            "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        ]
        for name_on_host, destination in (
            ("etc", "/etc"), ("var", "/var"), ("run", "/run"),
            ("boot", "/boot"), ("snapshots", "/.snapshots"),
        ):
            command += ["--bind", str(self.root / name_on_host), destination]
        for name_on_host, destination in (
            ("libexec", "/usr/libexec"), ("sbin", "/usr/sbin"),
            ("modules", "/usr/lib/modules"), ("bin", "/test-bin"), ("defaults", "/defaults"),
        ):
            command += ["--ro-bind", str(self.root / name_on_host), destination]
        command += ["--", "/bin/sh", "-s", "--", action]
        # Redirect external resources/commands, not conditions or control flow.
        source = (ROOT / "scripts" / name).read_text().replace(
            "/usr/share/anduinos-btrfs-snapshots-manager/defaults", "/defaults"
        ).replace("/usr/bin/systemd-detect-virt", "/test-bin/systemd-detect-virt")
        result = subprocess.run(
            command, input=source, capture_output=True, text=True, timeout=15,
            env={**os.environ, "PATH": "/test-bin:/usr/bin:/bin",
                 "TEST_FAIL": failure, "TEST_CHROOT_EXIT": "0" if chroot else "1"},
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
