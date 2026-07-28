import os
import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.chroot_env import EnterChrootStep, LeaveChrootStep
from installer_core.steps import InstallContext


class ChrootEnvironmentTests(unittest.TestCase):
    def test_run_is_private_and_temporary_files_are_restored(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            (target / "usr/sbin").mkdir(parents=True)
            resolver = target / "etc/resolv.conf"
            resolver.symlink_to("/run/systemd/resolve/stub-resolv.conf")
            policy = target / "usr/sbin/policy-rc.d"
            policy.write_text("#!/bin/sh\nexit 0\n")
            policy.chmod(0o700)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                values={"target": target},
            )

            EnterChrootStep(runner).execute(context)
            self.assertFalse(resolver.is_symlink())
            self.assertEqual(policy.read_text().splitlines()[-1], "exit 101")
            self.assertTrue(os.access(policy, os.X_OK))

            LeaveChrootStep(runner).execute(context)
            self.assertTrue(resolver.is_symlink())
            self.assertEqual(
                os.readlink(resolver),
                "/run/systemd/resolve/stub-resolv.conf",
            )
            self.assertEqual(policy.read_text(), "#!/bin/sh\nexit 0\n")
            self.assertEqual(policy.stat().st_mode & 0o777, 0o700)

        commands = [item[0] for item in runner.commands]
        self.assertIn(
            (
                "mount",
                "-t",
                "tmpfs",
                "-o",
                "mode=0755,nosuid,nodev",
                "tmpfs",
                str(target / "run"),
            ),
            commands,
        )
        self.assertNotIn(
            ("mount", "--rbind", "/run", str(target / "run")), commands
        )
        self.assertEqual(
            [command for command in commands if command[0] == "umount"],
            [
                ("umount", "--recursive", str(target / "run")),
                ("umount", "--recursive", str(target / "sys")),
                ("umount", "--recursive", str(target / "proc")),
                ("umount", "--recursive", str(target / "dev")),
            ],
        )

    def test_absent_policy_is_removed_on_leave(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            (target / "usr/sbin").mkdir(parents=True)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                values={"target": target},
            )
            EnterChrootStep(runner).execute(context)
            policy = target / "usr/sbin/policy-rc.d"
            self.assertTrue(policy.exists())
            LeaveChrootStep(runner).execute(context)
            self.assertFalse(policy.exists())

    def test_failed_unmount_remains_tracked_for_cleanup_retry(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            failed = target / "sys"
            command = ("umount", "--recursive", str(failed))
            runner.outputs[command] = ("", "busy", 1)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                values={
                    "target": target,
                    "chroot_mounts": [target / "dev", failed],
                    "chroot_environment_ready": True,
                },
            )
            step = LeaveChrootStep(runner)
            with self.assertRaisesRegex(RuntimeError, "Could not unmount"):
                step.execute(context)
            self.assertEqual(context.values["chroot_mounts"], [failed])

            runner.outputs[command] = ("", "", 0)
            step.cleanup(context)
            self.assertEqual(context.values["chroot_mounts"], [])
