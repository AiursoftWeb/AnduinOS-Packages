import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.remote_access import ProvisionRemoteAccessStep
from installer_core.steps import InstallContext


def package_query(target: Path, package: str) -> tuple[str, ...]:
    return (
        "chroot",
        str(target),
        "dpkg-query",
        "--show",
        "--showformat=${db:Status-Abbrev}",
        package,
    )


def context_for(
    target: Path, messages=None, *, ssh_password_login: bool = False
) -> InstallContext:
    log_messages = messages if messages is not None else []
    plan = valid_plan()
    plan = replace(
        plan,
        access=replace(
            plan.access, ssh_password_login=ssh_password_login
        ),
    )
    return InstallContext(
        plan,
        log_messages.append,
        values={
            "target": target,
            "chroot_environment_ready": True,
        },
    )


def write_generated_key(target: Path, mode: int = 0o600) -> None:
    private_key = target / "etc/ssh/ssh_host_ed25519_key"
    private_key.write_text("new private host identity\n")
    private_key.chmod(mode)
    public_key = private_key.with_name(private_key.name + ".pub")
    public_key.write_text("ssh-ed25519 generated-host-key\n")


class InspectingUfwRunner(FakeRunner):
    def __init__(self, config: Path, *, fail_ufw: bool = False):
        super().__init__()
        self.config = config
        self.fail_ufw = fail_ufw
        self.ufw_was_disabled_during_write = False

    def run(self, command, **kwargs):
        if tuple(command)[-3:] == ("ufw", "allow", "OpenSSH"):
            self.ufw_was_disabled_during_write = (
                "ENABLED=no" in self.config.read_text()
            )
            if self.fail_ufw:
                raise RuntimeError("injected UFW failure")
        return super().run(command, **kwargs)


class ProvisionRemoteAccessTests(unittest.TestCase):
    def test_replaces_live_identity_and_applies_safe_desktop_defaults(self):
        messages = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            ssh_dir = target / "etc/ssh"
            ssh_dir.mkdir(parents=True)
            policy = ssh_dir / "sshd_config.d/00-anduinos-installer.conf"
            policy.parent.mkdir()
            policy.write_text("PasswordAuthentication yes\n")
            stale_private = ssh_dir / "ssh_host_rsa_key"
            stale_public = ssh_dir / "ssh_host_rsa_key.pub"
            stale_private.write_text("copied Live private key\n")
            stale_public.write_text("copied Live public key\n")
            ufw_config = target / "etc/ufw/ufw.conf"
            ufw_config.parent.mkdir(parents=True)
            ufw_config.write_text("ENABLED=yes\n")
            runner = InspectingUfwRunner(ufw_config)
            runner.outputs[package_query(target, "openssh-server")] = (
                "ii ",
                "",
                0,
            )
            runner.outputs[package_query(target, "ufw")] = ("ii ", "", 0)
            context = context_for(target, messages)
            step = ProvisionRemoteAccessStep(runner)
            self.assertEqual(step.title, "Configure Secure Shell")

            step.preflight(context)
            step.execute(context)
            self.assertFalse(stale_private.exists())
            self.assertFalse(stale_public.exists())
            self.assertFalse(policy.exists())
            self.assertTrue(runner.ufw_was_disabled_during_write)
            self.assertEqual(ufw_config.read_text(), "ENABLED=yes\n")

            write_generated_key(target)
            for unit in ("ssh.service", "ssh.socket"):
                runner.outputs[
                    (
                        "systemctl",
                        f"--root={target}",
                        "is-enabled",
                        unit,
                    )
                ] = ("disabled\n", "", 1)
            runner.outputs[
                ("chroot", str(target), "ufw", "show", "added")
            ] = (
                "Added user rules:\nufw allow OpenSSH\n",
                "",
                0,
            )
            step.verify(context)

        commands = [command for command, _kwargs in runner.commands]
        self.assertEqual(runner.required, ["chroot", "systemctl"])
        self.assertIn(
            ("chroot", str(target), "ssh-keygen", "-A"), commands
        )
        self.assertIn(("chroot", str(target), "sshd", "-t"), commands)
        self.assertIn(
            (
                "systemctl",
                f"--root={target}",
                "disable",
                "ssh.service",
                "ssh.socket",
            ),
            commands,
        )
        self.assertIn(
            ("chroot", str(target), "ufw", "allow", "OpenSSH"),
            commands,
        )
        self.assertIn(
            "Removed copied Live SSH host identity before provisioning",
            messages,
        )

    def test_ufw_is_optional_but_openssh_server_is_a_composition_contract(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc/ssh").mkdir(parents=True)
            context = context_for(target)
            step = ProvisionRemoteAccessStep(runner)
            with self.assertRaisesRegex(RuntimeError, "openssh-server"):
                step.execute(context)

            runner.outputs[package_query(target, "openssh-server")] = (
                "ii ",
                "",
                0,
            )
            runner.outputs[package_query(target, "ufw")] = ("", "", 1)
            step.execute(context)

        commands = [command for command, _kwargs in runner.commands]
        self.assertNotIn(
            ("chroot", str(target), "ufw", "allow", "OpenSSH"),
            commands,
        )

    def test_password_login_writes_safe_policy_and_enables_socket_only(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc/ssh").mkdir(parents=True)
            runner.outputs[package_query(target, "openssh-server")] = (
                "ii ",
                "",
                0,
            )
            runner.outputs[package_query(target, "ufw")] = ("", "", 1)
            context = context_for(target, ssh_password_login=True)
            step = ProvisionRemoteAccessStep(runner)
            step.execute(context)

            policy = (
                target
                / "etc/ssh/sshd_config.d/00-anduinos-installer.conf"
            )
            self.assertEqual(
                policy.read_text(),
                "# Generated by the AnduinOS installer.\n"
                "PasswordAuthentication yes\n"
                "PermitEmptyPasswords no\n"
                "PermitRootLogin no\n",
            )
            self.assertEqual(policy.stat().st_mode & 0o777, 0o644)
            write_generated_key(target)
            runner.outputs[
                (
                    "systemctl",
                    f"--root={target}",
                    "is-enabled",
                    "ssh.service",
                )
            ] = ("disabled\n", "", 1)
            runner.outputs[
                (
                    "systemctl",
                    f"--root={target}",
                    "is-enabled",
                    "ssh.socket",
                )
            ] = ("enabled\n", "", 0)
            runner.outputs[
                (
                    "chroot",
                    str(target),
                    "sshd",
                    "-T",
                    "-C",
                    "user=alice,host=anduinos,addr=127.0.0.1",
                )
            ] = (
                "passwordauthentication yes\n"
                "permitemptypasswords no\n"
                "permitrootlogin no\n",
                "",
                0,
            )
            step.verify(context)

        commands = [command for command, _kwargs in runner.commands]
        self.assertIn(
            (
                "systemctl",
                f"--root={target}",
                "enable",
                "ssh.socket",
            ),
            commands,
        )
        self.assertNotIn(
            (
                "systemctl",
                f"--root={target}",
                "enable",
                "ssh.service",
            ),
            commands,
        )

    def test_restores_enabled_ufw_state_when_rule_creation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc/ssh").mkdir(parents=True)
            ufw_config = target / "etc/ufw/ufw.conf"
            ufw_config.parent.mkdir(parents=True)
            ufw_config.write_text("ENABLED=yes\n")
            runner = InspectingUfwRunner(ufw_config, fail_ufw=True)
            runner.outputs[package_query(target, "openssh-server")] = (
                "ii ",
                "",
                0,
            )
            runner.outputs[package_query(target, "ufw")] = ("ii ", "", 0)

            with self.assertRaisesRegex(RuntimeError, "injected UFW"):
                ProvisionRemoteAccessStep(runner).execute(
                    context_for(target)
                )

            self.assertTrue(runner.ufw_was_disabled_during_write)
            self.assertEqual(ufw_config.read_text(), "ENABLED=yes\n")

    def test_rejects_generated_private_keys_with_broad_permissions(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc/ssh").mkdir(parents=True)
            runner.outputs[package_query(target, "openssh-server")] = (
                "ii ",
                "",
                0,
            )
            runner.outputs[package_query(target, "ufw")] = ("", "", 1)
            context = context_for(target)
            step = ProvisionRemoteAccessStep(runner)
            step.execute(context)
            write_generated_key(target, mode=0o644)
            with self.assertRaisesRegex(RuntimeError, "permissions"):
                step.verify(context)


if __name__ == "__main__":
    unittest.main()
