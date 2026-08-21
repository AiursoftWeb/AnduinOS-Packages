import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.model import (
    AuthenticationMode,
)
from installer_core.steps import InstallContext
from installer_core.system_config import (
    ConfigureSystemStep,
    _set_passwordless_sudo,
)
from installer_core.validation import PlanValidationError, validate_plan


MACHINE_ID = "0123456789abcdef0123456789abcdef"


def prepare_target(root: Path, timezone: str) -> None:
    (root / "etc/default").mkdir(parents=True)
    (root / "etc/locale.gen").write_text("# en_US.UTF-8 UTF-8\n")
    zone = root / "usr/share/zoneinfo" / timezone
    zone.parent.mkdir(parents=True)
    zone.touch()


class ConfigureSystemTests(unittest.TestCase):
    def test_configures_account_region_and_machine_identity(self):
        plan = valid_plan()
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target, plan.regional.timezone)
            getent = (
                "chroot",
                str(target),
                "getent",
                "passwd",
                plan.identity.username,
            )
            runner.outputs[getent] = ("", "", 1)
            machine = (
                "systemd-machine-id-setup",
                f"--root={target}",
                "--print",
            )
            runner.outputs[machine] = (MACHINE_ID + "\n", "", 0)
            context = InstallContext(
                plan, lambda _message: None, values={"target": target}
            )
            step = ConfigureSystemStep(runner)
            step.execute(context)

            self.assertEqual(
                (target / "etc/hostname").read_text(), "anduinos\n"
            )
            self.assertIn(
                "127.0.1.1 anduinos", (target / "etc/hosts").read_text()
            )
            self.assertEqual(
                (target / "etc/default/locale").read_text(),
                'LANG="en_US.UTF-8"\nLANGUAGE="en_US:en"\n',
            )
            self.assertIn(
                "en_US.UTF-8 UTF-8", (target / "etc/locale.gen").read_text()
            )
            self.assertEqual(
                (target / "etc/timezone").read_text(), "Asia/Singapore\n"
            )
            self.assertEqual(
                (target / "etc/machine-id").read_text(), MACHINE_ID + "\n"
            )
            self.assertEqual(
                (target / "var/lib/dbus/machine-id").readlink(),
                Path("/etc/machine-id"),
            )
            runner.outputs[getent] = (
                "alice:x:1000:1000:Alice Example:/home/alice:/bin/bash\n",
                "",
                0,
            )
            runner.outputs[
                ("chroot", str(target), "id", "-nG", "alice")
            ] = ("alice sudo\n", "", 0)
            step.verify(context)

        commands = [item[0] for item in runner.commands]
        chpasswd = next(item for item in runner.commands if item[0][2] == "chpasswd")
        self.assertNotIn(plan.identity.password_hash, repr(commands))
        self.assertEqual(
            chpasswd[1]["input_text"],
            f"{plan.identity.username}:{plan.identity.password_hash}\n",
        )
        self.assertIn(
            ("chroot", str(target), "passwd", "--lock", "root"), commands
        )

    def test_passwordless_shared_account_autologs_in_and_has_nopasswd_sudo(self):
        plan = valid_plan(
            authentication=AuthenticationMode.PASSWORDLESS_SHARED
        )
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target, plan.regional.timezone)
            (target / "etc/gdm3").mkdir(parents=True)
            (target / "etc/gdm3/custom.conf").write_text(
                "[daemon]\n# AutomaticLoginEnable=false\n\n[security]\n"
            )
            getent = (
                "chroot",
                str(target),
                "getent",
                "passwd",
                plan.identity.username,
            )
            runner.outputs[getent] = ("", "", 1)
            runner.outputs[
                (
                    "systemd-machine-id-setup",
                    f"--root={target}",
                    "--print",
                )
            ] = (MACHINE_ID, "", 0)
            context = InstallContext(
                plan, lambda _message: None, {"target": target}
            )
            step = ConfigureSystemStep(runner)
            step.execute(context)

            sudoers = (
                target / "etc/sudoers.d/90-anduinos-passwordless-admin"
            )
            self.assertEqual(
                sudoers.read_text(),
                "alice ALL=(ALL:ALL) NOPASSWD: ALL\n",
            )
            self.assertEqual(sudoers.stat().st_mode & 0o777, 0o440)
            sudo_state = (
                target / "var/lib/anduinos-passwordless-sudo/users"
            )
            self.assertEqual(sudo_state.read_text(), "alice\n")
            self.assertEqual(sudo_state.stat().st_mode & 0o777, 0o644)
            gdm = (target / "etc/gdm3/custom.conf").read_text()
            self.assertIn("AutomaticLoginEnable=true", gdm)
            self.assertIn("AutomaticLogin=alice", gdm)
            self.assertIn("[security]", gdm)

            runner.outputs[getent] = (
                "alice:x:1000:1000:Alice Example:/home/alice:/bin/bash\n",
                "",
                0,
            )
            runner.outputs[
                ("chroot", str(target), "id", "-nG", "alice")
            ] = ("alice sudo\n", "", 0)
            step.verify(context)

        commands = [item[0] for item in runner.commands]
        self.assertIn(
            ("chroot", str(target), "passwd", "--delete", "alice"),
            commands,
        )
        self.assertIn(
            (
                "chroot",
                str(target),
                "visudo",
                "--check",
                "--file",
                "/etc/sudoers",
            ),
            commands,
        )
        self.assertFalse(any(command[2] == "chpasswd" for command in commands))

    def test_password_account_can_explicitly_enable_nopasswd_sudo(self):
        plan = valid_plan(sudo_without_password=True)
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target, plan.regional.timezone)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "getent",
                    "passwd",
                    plan.identity.username,
                )
            ] = ("", "", 1)
            runner.outputs[
                (
                    "systemd-machine-id-setup",
                    f"--root={target}",
                    "--print",
                )
            ] = (MACHINE_ID, "", 0)
            messages = []
            context = InstallContext(
                plan, messages.append, {"target": target}
            )
            step = ConfigureSystemStep(runner)
            step.execute(context)
            sudoers = (
                target / "etc/sudoers.d/90-anduinos-passwordless-admin"
            )
            self.assertEqual(
                sudoers.read_text(),
                "alice ALL=(ALL:ALL) NOPASSWD: ALL\n",
            )
            self.assertEqual(
                (
                    target / "var/lib/anduinos-passwordless-sudo/users"
                ).read_text(),
                "alice\n",
            )
            gdm = (target / "etc/gdm3/custom.conf").read_text()
            self.assertIn("AutomaticLoginEnable=false", gdm)
            self.assertNotIn("AutomaticLogin=alice", gdm)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "getent",
                    "passwd",
                    plan.identity.username,
                )
            ] = (
                "alice:x:1000:1000:Alice Example:/home/alice:/bin/bash\n",
                "",
                0,
            )
            runner.outputs[
                ("chroot", str(target), "id", "-nG", "alice")
            ] = ("alice sudo\n", "", 0)
            step.verify(context)
            self.assertIn("Account login: password authentication", messages)
            self.assertIn("GDM automatic login: disabled", messages)
            self.assertIn(
                "Sudo authentication: password not required", messages
            )

    def test_passwordless_sudo_state_is_atomic_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            _set_passwordless_sudo(target, "alice", enabled=True)
            policy = target / "etc/sudoers.d/90-anduinos-passwordless-admin"
            state = target / "var/lib/anduinos-passwordless-sudo/users"
            self.assertEqual(
                policy.read_text(),
                "alice ALL=(ALL:ALL) NOPASSWD: ALL\n",
            )
            self.assertEqual(state.read_text(), "alice\n")

            _set_passwordless_sudo(target, "alice", enabled=False)
            self.assertFalse(policy.exists())
            self.assertEqual(state.read_text(), "")

            victim = target / "victim"
            victim.write_text("keep\n")
            policy.symlink_to(victim)
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                _set_passwordless_sudo(target, "alice", enabled=False)
            self.assertEqual(victim.read_text(), "keep\n")

    def test_failed_full_sudoers_validation_restores_policy_and_state(self):
        class RejectChangedSudoers(FakeRunner):
            def __init__(self):
                super().__init__()
                self.visudo_checks = 0

            def run(self, command, **kwargs):
                if tuple(command)[2:3] == ("visudo",):
                    self.visudo_checks += 1
                    if self.visudo_checks == 2:
                        raise RuntimeError("visudo rejected changed policy")
                return super().run(command, **kwargs)

        plan = valid_plan(sudo_without_password=True)
        runner = RejectChangedSudoers()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target, plan.regional.timezone)
            policy = target / "etc/sudoers.d/90-anduinos-passwordless-admin"
            policy.parent.mkdir(parents=True)
            policy.write_text("bob ALL=(ALL:ALL) NOPASSWD: ALL\n")
            policy.chmod(0o440)
            state = target / "var/lib/anduinos-passwordless-sudo/users"
            state.parent.mkdir(parents=True)
            state.write_text("bob\n")
            state.chmod(0o644)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "getent",
                    "passwd",
                    plan.identity.username,
                )
            ] = ("", "", 1)
            context = InstallContext(
                plan, lambda _message: None, {"target": target}
            )

            with self.assertRaisesRegex(RuntimeError, "visudo rejected"):
                ConfigureSystemStep(runner).execute(context)

            self.assertEqual(
                policy.read_text(), "bob ALL=(ALL:ALL) NOPASSWD: ALL\n"
            )
            self.assertEqual(policy.stat().st_mode & 0o777, 0o440)
            self.assertEqual(state.read_text(), "bob\n")
            self.assertEqual(state.stat().st_mode & 0o777, 0o644)

    def test_password_account_can_autologin_without_removing_its_password(self):
        plan = valid_plan(automatic_login=True)
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target, plan.regional.timezone)
            (target / "etc/gdm3").mkdir(parents=True)
            (target / "etc/gdm3/custom.conf").write_text(
                "[daemon]\nAutomaticLoginEnable=false\n\n[security]\n"
            )
            getent = (
                "chroot",
                str(target),
                "getent",
                "passwd",
                plan.identity.username,
            )
            runner.outputs[getent] = ("", "", 1)
            runner.outputs[
                (
                    "systemd-machine-id-setup",
                    f"--root={target}",
                    "--print",
                )
            ] = (MACHINE_ID, "", 0)
            context = InstallContext(
                plan, lambda _message: None, {"target": target}
            )
            step = ConfigureSystemStep(runner)
            step.execute(context)

            gdm = (target / "etc/gdm3/custom.conf").read_text()
            self.assertIn("AutomaticLoginEnable=true", gdm)
            self.assertIn("AutomaticLogin=alice", gdm)
            self.assertIn("[security]", gdm)

        commands = [command for command, _kwargs in runner.commands]
        self.assertIn(
            ("chroot", str(target), "chpasswd", "--encrypted"), commands
        )
        self.assertNotIn(
            ("chroot", str(target), "passwd", "--delete", "alice"),
            commands,
        )

    def test_rejects_gecos_control_characters(self):
        base = valid_plan()
        plan = replace(
            base,
            identity=replace(base.identity, full_name="Alice:root"),
        )
        with self.assertRaises(PlanValidationError):
            validate_plan(plan)

    def test_rejects_password_hash_line_injection(self):
        base = valid_plan()
        plan = replace(
            base,
            identity=replace(
                base.identity,
                password_hash="$y$j9T$valid\nroot:$y$j9T$attacker",
            ),
        )
        with self.assertRaises(PlanValidationError):
            validate_plan(plan)
