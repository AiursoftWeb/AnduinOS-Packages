"""Configure identity and regional settings in the copied target system."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .model import AuthenticationMode
from .steps import FailurePolicy, InstallContext


MACHINE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PASSWORDLESS_SUDOERS = Path("etc/sudoers.d/90-anduinos-passwordless-admin")
PASSWORDLESS_SUDO_STATE = Path("var/lib/anduinos-passwordless-sudo/users")


@dataclass
class ConfigureSystemStep:
    runner: CommandRunner
    id: str = "configure-system"
    title: str = "Configure account, region, timezone, and machine identity"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 5
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot", "systemd-machine-id-setup"))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        plan = context.plan
        _write_hostname(target, plan.identity.hostname)
        _write_locale(target, plan.regional.locale)
        _write_timezone(target, plan.regional.timezone)
        self.runner.run(
            ("chroot", str(target), "locale-gen", plan.regional.locale),
            timeout=300,
        )
        self._create_user(context, target)
        self._create_machine_id(target)

    def _create_user(self, context: InstallContext, target: Path) -> None:
        identity = context.plan.identity
        existing = self.runner.run(
            ("chroot", str(target), "getent", "passwd", identity.username),
            check=False,
            timeout=10,
        )
        if existing.returncode == 0:
            raise RuntimeError(
                f"Target user already exists: {identity.username}"
            )
        self.runner.run(
            (
                "chroot",
                str(target),
                "useradd",
                "--create-home",
                "--shell",
                "/bin/bash",
                "--comment",
                identity.full_name,
                "--groups",
                "sudo",
                identity.username,
            ),
            timeout=30,
        )
        if identity.authentication is AuthenticationMode.PASSWORD:
            # The hash is carried over stdin: it is absent from argv and logs.
            self.runner.run(
                ("chroot", str(target), "chpasswd", "--encrypted"),
                input_text=f"{identity.username}:{identity.password_hash}\n",
                timeout=30,
            )
            context.log("Account login: password authentication")
        else:
            self.runner.run(
                ("chroot", str(target), "passwd", "--delete", identity.username),
                timeout=30,
            )
            context.log("Account login: passwordless shared account")

        _write_gdm_autologin(
            target,
            identity.username,
            enabled=context.plan.access.automatic_login,
        )
        if context.plan.access.automatic_login:
            context.log(
                f"GDM automatic login: enabled for {identity.username}"
            )
        else:
            context.log("GDM automatic login: disabled")

        sudoers_backup = _snapshot_file(_passwordless_sudo_path(target))
        state_backup = _snapshot_file(_passwordless_sudo_state_path(target))
        self.runner.run(
            (
                "chroot",
                str(target),
                "visudo",
                "--check",
                "--file",
                "/etc/sudoers",
            ),
            timeout=30,
        )
        try:
            _set_passwordless_sudo(
                target,
                identity.username,
                enabled=context.plan.access.sudo_without_password,
            )
            self.runner.run(
                (
                    "chroot",
                    str(target),
                    "visudo",
                    "--check",
                    "--file",
                    "/etc/sudoers",
                ),
                timeout=30,
            )
        except BaseException:
            _restore_file(_passwordless_sudo_path(target), sudoers_backup)
            _restore_file(_passwordless_sudo_state_path(target), state_backup)
            raise

        if context.plan.access.sudo_without_password:
            context.log("Sudo authentication: password not required")
        else:
            context.log("Sudo authentication: account password required")
        self.runner.run(
            ("chroot", str(target), "passwd", "--lock", "root"),
            timeout=30,
        )

    def _create_machine_id(self, target: Path) -> None:
        machine_id_path = target / "etc/machine-id"
        machine_id_path.parent.mkdir(parents=True, exist_ok=True)
        if machine_id_path.is_symlink():
            machine_id_path.unlink()
        machine_id_path.write_text("", encoding="ascii")

        # Never let systemd-machine-id-setup reuse the live image's D-Bus ID.
        dbus_id = target / "var/lib/dbus/machine-id"
        dbus_id.parent.mkdir(parents=True, exist_ok=True)
        if dbus_id.exists() or dbus_id.is_symlink():
            dbus_id.unlink()
        result = self.runner.run(
            (
                "systemd-machine-id-setup",
                f"--root={target}",
                "--print",
            ),
            timeout=30,
        )
        machine_id = machine_id_path.read_text(encoding="ascii").strip()
        if not MACHINE_ID_RE.fullmatch(machine_id):
            # This fallback also makes the command boundary easy to simulate.
            reported = result.stdout.strip().lower()
            if MACHINE_ID_RE.fullmatch(reported):
                machine_id_path.write_text(reported + "\n", encoding="ascii")
                machine_id = reported
        if not MACHINE_ID_RE.fullmatch(machine_id):
            raise RuntimeError("Failed to create a valid machine-id")

        dbus_id.symlink_to("/etc/machine-id")

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        plan = context.plan
        if (target / "etc/hostname").read_text().strip() != plan.identity.hostname:
            raise RuntimeError("Hostname verification failed")
        if (
            target / "etc/timezone"
        ).read_text().strip() != plan.regional.timezone:
            raise RuntimeError("Timezone verification failed")
        locale = (target / "etc/default/locale").read_text()
        if f'LANG="{plan.regional.locale}"' not in locale:
            raise RuntimeError("Locale verification failed")
        if not MACHINE_ID_RE.fullmatch(
            (target / "etc/machine-id").read_text().strip()
        ):
            raise RuntimeError("machine-id verification failed")
        account = self.runner.run(
            ("chroot", str(target), "getent", "passwd", plan.identity.username),
            timeout=10,
        ).stdout
        if not account.startswith(f"{plan.identity.username}:"):
            raise RuntimeError("User account verification failed")
        group = self.runner.run(
            ("chroot", str(target), "id", "-nG", plan.identity.username),
            timeout=10,
        ).stdout.split()
        if "sudo" not in group:
            raise RuntimeError("User is not a member of sudo")
        sudoers = _passwordless_sudo_path(target)
        sudo_state = _passwordless_sudo_state_path(target)
        gdm = target / "etc/gdm3/custom.conf"
        gdm_text = gdm.read_text(encoding="utf-8") if gdm.is_file() else ""
        if plan.access.sudo_without_password:
            if (
                sudoers.is_symlink()
                or not sudoers.is_file()
                or sudoers.stat().st_mode & 0o777 != 0o440
            ):
                raise RuntimeError("Passwordless sudo policy is missing or unsafe")
            expected = (
                f"{plan.identity.username} ALL=(ALL:ALL) NOPASSWD: ALL\n"
            )
            if sudoers.read_text(encoding="utf-8") != expected:
                raise RuntimeError("Passwordless sudo policy verification failed")
            if (
                sudo_state.is_symlink()
                or not sudo_state.is_file()
                or sudo_state.stat().st_mode & 0o777 != 0o644
                or sudo_state.read_text(encoding="utf-8")
                != f"{plan.identity.username}\n"
            ):
                raise RuntimeError("Passwordless sudo state verification failed")
        elif sudoers.exists():
            raise RuntimeError("Unexpected passwordless sudo policy")
        elif (
            sudo_state.is_symlink()
            or not sudo_state.is_file()
            or sudo_state.stat().st_mode & 0o777 != 0o644
            or sudo_state.read_text(encoding="utf-8") != ""
        ):
            raise RuntimeError("Unexpected passwordless sudo state")

        self.runner.run(
            (
                "chroot",
                str(target),
                "visudo",
                "--check",
                "--file",
                "/etc/sudoers",
            ),
            timeout=30,
        )

        expects_autologin = plan.access.automatic_login
        if expects_autologin:
            if (
                "AutomaticLoginEnable=true" not in gdm_text
                or f"AutomaticLogin={plan.identity.username}" not in gdm_text
            ):
                raise RuntimeError("Passwordless automatic login is not configured")
        elif (
            "AutomaticLoginEnable=false" not in gdm_text
            or f"AutomaticLogin={plan.identity.username}" in gdm_text
        ):
            raise RuntimeError(
                "Automatic login was not disabled for password authentication"
            )

    def cleanup(self, context: InstallContext) -> None:
        return None


def _write_hostname(target: Path, hostname: str) -> None:
    etc = target / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hostname").write_text(hostname + "\n", encoding="utf-8")
    (etc / "hosts").write_text(
        "127.0.0.1 localhost\n"
        f"127.0.1.1 {hostname}\n"
        "\n"
        "::1 localhost ip6-localhost ip6-loopback\n"
        "ff02::1 ip6-allnodes\n"
        "ff02::2 ip6-allrouters\n",
        encoding="utf-8",
    )


def _passwordless_sudo_path(target: Path) -> Path:
    return target / PASSWORDLESS_SUDOERS


def _passwordless_sudo_state_path(target: Path) -> Path:
    return target / PASSWORDLESS_SUDO_STATE


def _set_passwordless_sudo(
    target: Path, username: str, *, enabled: bool
) -> None:
    path = _passwordless_sudo_path(target)
    if enabled:
        _atomic_write(
            path,
            f"{username} ALL=(ALL:ALL) NOPASSWD: ALL\n",
            0o440,
        )
    elif path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Passwordless sudo policy path is unsafe")
        path.unlink()
    _atomic_write(
        _passwordless_sudo_state_path(target),
        f"{username}\n" if enabled else "",
        0o644,
    )


def _snapshot_file(path: Path) -> tuple[bytes, int] | None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Unsafe policy path: {path}")
    if not path.exists():
        return None
    return path.read_bytes(), path.stat().st_mode & 0o777


def _restore_file(path: Path, snapshot: tuple[bytes, int] | None) -> None:
    if snapshot is None:
        if path.exists() or path.is_symlink():
            path.unlink()
        return
    content, mode = snapshot
    _atomic_write(path, content.decode("utf-8"), mode)


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_gdm_autologin(
    target: Path, username: str, *, enabled: bool
) -> None:
    path = target / "etc/gdm3/custom.conf"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_text(encoding="utf-8") if path.is_file() else ""
    section = re.search(
        r"(?ms)^(\[daemon\]\s*\n)(.*?)(?=^\[|\Z)", content
    )
    settings = (
        "AutomaticLoginEnable=true\n"
        f"AutomaticLogin={username}\n"
        if enabled
        else "AutomaticLoginEnable=false\n"
    )
    if section:
        body = re.sub(
            r"(?mi)^\s*#?\s*AutomaticLogin(?:Enable)?\s*=.*\n?",
            "",
            section.group(2),
        )
        replacement = section.group(1) + settings + body
        content = content[: section.start()] + replacement + content[section.end() :]
    else:
        content = content.rstrip() + "\n\n[daemon]\n" + settings
    path.write_text(content, encoding="utf-8")


def _write_locale(target: Path, locale: str) -> None:
    etc = target / "etc"
    default = etc / "default"
    default.mkdir(parents=True, exist_ok=True)
    gettext_locale = locale.removesuffix(".UTF-8")
    language = gettext_locale.partition("_")[0]
    (default / "locale").write_text(
        f'LANG="{locale}"\n'
        f'LANGUAGE="{gettext_locale}:{language}"\n',
        encoding="utf-8",
    )

    locale_gen = etc / "locale.gen"
    content = locale_gen.read_text(encoding="utf-8") if locale_gen.exists() else ""
    pattern = re.compile(rf"^\s*#?\s*{re.escape(locale)}\s+UTF-8\s*$", re.MULTILINE)
    replacement = f"{locale} UTF-8"
    if pattern.search(content):
        content = pattern.sub(replacement, content)
    else:
        content = content.rstrip() + "\n" + replacement + "\n"
    locale_gen.write_text(content, encoding="utf-8")


def _write_timezone(target: Path, timezone: str) -> None:
    zone = target / "usr/share/zoneinfo" / timezone
    if not zone.is_file():
        raise RuntimeError(f"Timezone data is missing: {timezone}")
    etc = target / "etc"
    (etc / "timezone").write_text(timezone + "\n", encoding="utf-8")
    localtime = etc / "localtime"
    if localtime.exists() or localtime.is_symlink():
        localtime.unlink()
    localtime.symlink_to(f"/usr/share/zoneinfo/{timezone}")


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target
