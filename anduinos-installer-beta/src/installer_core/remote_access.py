"""Provision target-owned OpenSSH identity and safe desktop defaults."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .steps import FailurePolicy, InstallContext


OPENSSH_SERVER_PACKAGE = "openssh-server"
UFW_PACKAGE = "ufw"
SSH_UNITS = ("ssh.service", "ssh.socket")
PASSWORD_LOGIN_DROP_IN = "00-anduinos-installer.conf"


@dataclass
class ProvisionRemoteAccessStep:
    runner: CommandRunner
    id: str = "provision-remote-access"
    title: str = "Provision Secure Shell access"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 2
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot", "systemctl"))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        if not _is_installed(
            self.runner, target, OPENSSH_SERVER_PACKAGE
        ):
            raise RuntimeError(
                "The desktop image does not contain openssh-server"
            )

        removed = _remove_host_keys(target)
        if removed:
            context.log(
                "Removed copied Live SSH host identity before provisioning"
            )

        self.runner.run(
            ("chroot", str(target), "ssh-keygen", "-A"),
            timeout=120,
        )
        _configure_password_login(
            target,
            enabled=context.plan.access.ssh_password_login,
        )
        self.runner.run(
            ("chroot", str(target), "sshd", "-t"),
            timeout=30,
        )

        # This is a newly created target, never an upgrade of an existing
        # machine. Reset both units explicitly so a Live-session toggle cannot
        # leak into the installation. Do not ship a global preset: preset-all
        # must never disconnect an administrator's established SSH service.
        self.runner.run(
            (
                "systemctl",
                f"--root={target}",
                "disable",
                *SSH_UNITS,
            ),
            timeout=30,
        )
        if context.plan.access.ssh_password_login:
            self.runner.run(
                (
                    "systemctl",
                    f"--root={target}",
                    "enable",
                    "ssh.socket",
                ),
                timeout=30,
            )
            context.log("Enabled Secure Shell password login")

        ufw_installed = _is_installed(self.runner, target, UFW_PACKAGE)
        context.values["remote_access_ufw_managed"] = ufw_installed
        if ufw_installed:
            # This rule alone opens no listener. It makes the administrator's
            # later GNOME Secure Shell opt-in work when UFW is active.
            _prepare_ufw_rule(self.runner, target)
            context.log("Prepared the UFW OpenSSH application rule")
        else:
            context.log("UFW is not installed; skipped its OpenSSH rule")

        context.values["remote_access_provisioned"] = True

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        if not context.values.get("remote_access_provisioned"):
            raise RuntimeError("Secure Shell provisioning did not execute")

        _verify_host_keys(target)
        self.runner.run(
            ("chroot", str(target), "sshd", "-t"),
            timeout=30,
        )
        for unit in SSH_UNITS:
            result = self.runner.run(
                ("systemctl", f"--root={target}", "is-enabled", unit),
                check=False,
                timeout=30,
            )
            state = result.stdout.strip().casefold()
            expected = (
                "enabled"
                if unit == "ssh.socket"
                and context.plan.access.ssh_password_login
                else "disabled"
            )
            if state != expected:
                raise RuntimeError(
                    f"Secure Shell unit state is incorrect: "
                    f"{unit}={state or 'unknown'}"
                )

        _verify_password_login(
            target,
            enabled=context.plan.access.ssh_password_login,
        )
        if context.plan.access.ssh_password_login:
            effective = self.runner.run(
                (
                    "chroot",
                    str(target),
                    "sshd",
                    "-T",
                    "-C",
                    f"user={context.plan.identity.username},"
                    f"host={context.plan.identity.hostname},"
                    "addr=127.0.0.1",
                ),
                timeout=30,
            ).stdout
            settings = {
                key.casefold(): value.casefold()
                for line in effective.splitlines()
                if len(parts := line.split(None, 1)) == 2
                for key, value in (parts,)
            }
            expected_settings = {
                "passwordauthentication": "yes",
                "permitemptypasswords": "no",
                "permitrootlogin": "no",
            }
            if any(
                settings.get(key) != value
                for key, value in expected_settings.items()
            ):
                raise RuntimeError(
                    "Effective SSH password-login policy is unsafe"
                )

        if context.values.get("remote_access_ufw_managed"):
            result = self.runner.run(
                ("chroot", str(target), "ufw", "show", "added"),
                timeout=30,
            )
            rules = {
                line.strip().casefold() for line in result.stdout.splitlines()
            }
            if "ufw allow openssh" not in rules:
                raise RuntimeError("The UFW OpenSSH rule was not preserved")

    def cleanup(self, context: InstallContext) -> None:
        return None


def _remove_host_keys(target: Path) -> tuple[Path, ...]:
    ssh_dir = target / "etc/ssh"
    if not ssh_dir.is_dir():
        raise RuntimeError("The target OpenSSH configuration is missing")

    candidates = {
        *ssh_dir.glob("ssh_host_*_key"),
        *ssh_dir.glob("ssh_host_*_key.pub"),
    }
    removed = []
    for path in sorted(candidates):
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed.append(path)
        elif path.exists():
            raise RuntimeError(f"Unexpected SSH host-key path: {path}")
    return tuple(removed)


def _password_login_path(target: Path) -> Path:
    return target / "etc/ssh/sshd_config.d" / PASSWORD_LOGIN_DROP_IN


def _configure_password_login(target: Path, *, enabled: bool) -> None:
    path = _password_login_path(target)
    if path.is_symlink():
        path.unlink()
    if not enabled:
        if path.exists():
            if not path.is_file():
                raise RuntimeError(f"Unexpected SSH policy path: {path}")
            path.unlink()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Generated by the AnduinOS installer.\n"
        "PasswordAuthentication yes\n"
        "PermitEmptyPasswords no\n"
        "PermitRootLogin no\n",
        encoding="ascii",
    )
    path.chmod(0o644)


def _verify_password_login(target: Path, *, enabled: bool) -> None:
    path = _password_login_path(target)
    if not enabled:
        if path.exists() or path.is_symlink():
            raise RuntimeError("Unexpected installer SSH password policy")
        return
    expected = (
        "# Generated by the AnduinOS installer.\n"
        "PasswordAuthentication yes\n"
        "PermitEmptyPasswords no\n"
        "PermitRootLogin no\n"
    )
    if (
        path.is_symlink()
        or not path.is_file()
        or path.read_text(encoding="ascii") != expected
        or path.stat().st_mode & 0o777 != 0o644
    ):
        raise RuntimeError("SSH password-login policy verification failed")


def _verify_host_keys(target: Path) -> None:
    ssh_dir = target / "etc/ssh"
    private_keys = tuple(sorted(ssh_dir.glob("ssh_host_*_key")))
    if not private_keys:
        raise RuntimeError("No target-owned SSH host keys were generated")

    for private_key in private_keys:
        public_key = private_key.with_name(private_key.name + ".pub")
        if (
            private_key.is_symlink()
            or not private_key.is_file()
            or private_key.stat().st_size == 0
        ):
            raise RuntimeError(f"Invalid SSH host private key: {private_key}")
        if private_key.stat().st_mode & 0o077:
            raise RuntimeError(
                f"SSH host private key permissions are too broad: "
                f"{private_key}"
            )
        if (
            public_key.is_symlink()
            or not public_key.is_file()
            or public_key.stat().st_size == 0
        ):
            raise RuntimeError(f"Missing SSH host public key: {public_key}")


def _prepare_ufw_rule(runner: CommandRunner, target: Path) -> None:
    """Write target UFW policy without touching the Live netfilter table."""

    config = target / "etc/ufw/ufw.conf"
    if config.is_symlink() or not config.is_file():
        raise RuntimeError("The target UFW configuration is missing")
    original = config.read_bytes()
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(
            "The target UFW configuration is invalid"
        ) from error
    enabled = re.search(r"(?m)^ENABLED=(yes|no)[ \t]*$", text)
    if enabled is None:
        raise RuntimeError("The target UFW enabled state is missing")

    original_stat = config.stat()
    temporarily_disabled = enabled.group(1) == "yes"
    if temporarily_disabled:
        disabled = (
            text[: enabled.start(1)] + "no" + text[enabled.end(1) :]
        ).encode("utf-8")
        _replace_file(config, disabled, original_stat)
    try:
        # UFW reloads the running netfilter table when its config says it is
        # enabled. Temporarily presenting the target as disabled makes this a
        # file-only operation even though chroot shares the Live kernel.
        runner.run(
            ("chroot", str(target), "ufw", "allow", "OpenSSH"),
            timeout=60,
        )
    finally:
        if temporarily_disabled:
            _replace_file(config, original, original_stat)


def _replace_file(
    path: Path,
    content: bytes,
    original_stat: os.stat_result,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, original_stat.st_mode & 0o7777)
        os.fchown(descriptor, original_stat.st_uid, original_stat.st_gid)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _is_installed(
    runner: CommandRunner,
    target: Path,
    package: str,
) -> bool:
    result = runner.run(
        (
            "chroot",
            str(target),
            "dpkg-query",
            "--show",
            "--showformat=${db:Status-Abbrev}",
            package,
        ),
        check=False,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.startswith("ii ")


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    if not context.values.get("chroot_environment_ready"):
        raise RuntimeError("Target chroot environment is not ready")
    return target
