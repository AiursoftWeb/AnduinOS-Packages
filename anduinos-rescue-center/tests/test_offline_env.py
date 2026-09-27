import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from anduinos_rescue_center.model import Partition
from anduinos_rescue_center.offline_env import _provide_dns, emergency_shell, opened_system


def partition() -> Partition:
    return Partition(
        path="/dev/sda2", parent="/dev/sda", size_bytes=10**10,
        filesystem="ext4", filesystem_uuid="root", label="", partuuid="part",
        mountpoints=(), identity="a" * 64,
    )


def btrfs_partition() -> Partition:
    return Partition(
        path="/dev/vda4", parent="/dev/vda", size_bytes=10**10,
        filesystem="btrfs", filesystem_uuid="root", label="", partuuid="part",
        mountpoints=(), identity="b" * 64,
    )


@contextmanager
def fake_writable(_path, _filesystem, *, mount_base, **_kwargs):
    yield mount_base


class OfflineEnvironmentTests(unittest.TestCase):
    def test_btrfs_chroot_mounts_root_subvolume_as_its_own_mountpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            (top / "@root/usr/lib").mkdir(parents=True)
            (top / "@root/usr/lib/os-release").write_text("ID=anduinos\n", encoding="utf-8")
            commands = []

            def run(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (patch("anduinos_rescue_center.offline_env.resolve_target", return_value=btrfs_partition()),
                  patch("anduinos_rescue_center.offline_env.mounted_writable", fake_writable)):
                with opened_system("/dev/vda4", "b" * 64, run=run, mount_base=top) as (root, _):
                    self.assertNotEqual(root, top / "@root")
                    self.assertEqual(commands[0][0:6],
                                     ["mount", "-t", "btrfs", "-o", "rw,subvol=@root", "--"])
                    self.assertEqual(commands[0][-2:], ["/dev/vda4", str(root)])
                    self.assertEqual(commands[1][0:2], ["mount", "--rbind"])
            self.assertEqual(commands[-1], ["umount", "--", str(root)])

    def test_failed_btrfs_root_mount_never_enters_chroot_or_leaves_mountpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            (top / "@root/usr/lib").mkdir(parents=True)
            (top / "@root/usr/lib/os-release").write_text("ID=anduinos\n", encoding="utf-8")
            commands = []

            def run(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 1, "", "mount failed")

            with (patch("anduinos_rescue_center.offline_env.resolve_target", return_value=btrfs_partition()),
                  patch("anduinos_rescue_center.offline_env.mounted_writable", fake_writable)):
                with self.assertRaisesRegex(RuntimeError, "mount failed"):
                    with opened_system("/dev/vda4", "b" * 64, run=run, mount_base=top):
                        pass
            self.assertEqual(len(commands), 1)
            self.assertEqual(list(top.glob("system-*")), [])

    def test_temporary_chroot_run_gets_real_dns_not_host_stub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source-resolv.conf"
            runtime = root / "run"
            runtime.mkdir()
            source.write_text("nameserver 127.0.0.53\n", encoding="utf-8")
            _provide_dns(runtime, (source,))
            self.assertFalse((runtime / "systemd/resolve").exists())
            source.write_text("nameserver 1.1.1.1\n", encoding="utf-8")
            _provide_dns(runtime, (source,))
            self.assertEqual((runtime / "systemd/resolve/stub-resolv.conf").read_text(),
                             "nameserver 1.1.1.1\n")

    def test_chroot_mounts_are_removed_in_reverse_order_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usr/lib").mkdir(parents=True)
            (root / "usr/lib/os-release").write_text("ID=anduinos\n", encoding="utf-8")
            calls = []

            def run(command, **_kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (patch("anduinos_rescue_center.offline_env.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.offline_env.mounted_writable", fake_writable)):
                with opened_system("/dev/sda2", "a" * 64, run=run, mount_base=root) as (target, _):
                    self.assertEqual(target, root)
            unmounts = [item[-1] for item in calls if item[0] == "umount"]
            self.assertEqual(unmounts, [str(root / item) for item in ("run", "proc", "sys", "dev")])
            self.assertIn(["mount", "--make-rslave", str(root / "dev")], calls)

    def test_symlinked_target_mount_directory_is_rejected_before_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usr/lib").mkdir(parents=True)
            (root / "usr/lib/os-release").write_text("ID=anduinos\n", encoding="utf-8")
            (root / "dev").symlink_to("/dev")
            calls = []

            def run(command, **_kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (patch("anduinos_rescue_center.offline_env.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.offline_env.mounted_writable", fake_writable)):
                with self.assertRaisesRegex(RuntimeError, "Unsafe target mount directory"):
                    with opened_system("/dev/sda2", "a" * 64, run=run, mount_base=root):
                        pass
            self.assertEqual(calls, [])

    def test_partial_mount_failure_unmounts_every_completed_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usr/lib").mkdir(parents=True)
            (root / "usr/lib/os-release").write_text("ID=anduinos\n", encoding="utf-8")
            calls = []

            def run(command, **_kwargs):
                calls.append(command)
                if command[:4] == ["mount", "-t", "proc", "proc"]:
                    return subprocess.CompletedProcess(command, 1, "", "proc mount failed")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (patch("anduinos_rescue_center.offline_env.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.offline_env.mounted_writable", fake_writable)):
                with self.assertRaisesRegex(RuntimeError, "proc mount failed"):
                    with opened_system("/dev/sda2", "a" * 64, run=run, mount_base=root):
                        pass
            unmounts = [item[-1] for item in calls if item[0] == "umount"]
            self.assertEqual(unmounts, [str(root / "sys"), str(root / "dev")])

    def test_emergency_shell_requires_live_tty_and_uses_selected_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "bin/bash").write_bytes(b"shell")
            calls = []

            @contextmanager
            def opened(*_args, **_kwargs):
                yield root, partition()

            def run(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0)

            with (patch("anduinos_rescue_center.live.is_trusted_live_environment", return_value=True),
                  patch("anduinos_rescue_center.offline_env.os.isatty", return_value=True),
                  patch("anduinos_rescue_center.offline_env.opened_system", opened),
                  patch("sys.stdout", new_callable=StringIO)):
                self.assertEqual(emergency_shell("/dev/sda2", "a" * 64, run=run), 0)
            self.assertEqual(calls[0][0], ["chroot", str(root), "/bin/bash"])
            self.assertEqual(calls[0][1]["env"]["HOME"], "/root")


if __name__ == "__main__":
    unittest.main()
