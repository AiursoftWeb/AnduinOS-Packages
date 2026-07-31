"""Optional package operations performed inside the isolated target."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandRunner
from .mirrors import restore_original_mirror
from .steps import FailurePolicy, InstallContext, StepWarning
from .validation import validate_plan


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    if not context.values.get("chroot_environment_ready"):
        raise RuntimeError("Target chroot environment is not ready")
    return target


def _require_target_command(target: Path, relative: str) -> None:
    if not (target / relative).is_file():
        raise RuntimeError(f"Target command is missing: /{relative}")


def refresh_package_indexes(
    context: InstallContext,
    runner: CommandRunner,
) -> None:
    """Refresh target indexes with mirror rollback for package consumers."""

    context.values["package_indexes_refreshed"] = False
    target = _target(context)
    _require_target_command(target, "usr/bin/apt-get")
    command = (
        "chroot",
        str(target),
        "/usr/bin/env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "-o",
        "Acquire::Retries=1",
        "-o",
        "Acquire::http::Timeout=15",
        "-o",
        "Acquire::https::Timeout=15",
        "update",
    )
    result = runner.run(command, check=False, timeout=1800)
    if result.returncode != 0:
        if restore_original_mirror(context):
            context.log(
                "Selected mirror failed apt update; restored original sources"
            )
            result = runner.run(command, check=False, timeout=1800)
        if result.returncode != 0:
            raise CommandError(
                "Could not refresh package indexes; continuing with the "
                "installation media's package set"
            )
    context.values["package_indexes_refreshed"] = True


@dataclass
class RefreshPackageIndexesStep:
    """Refresh indexes, but preserve a usable offline installation."""

    runner: CommandRunner
    id: str = "refresh-package-indexes"
    title: str = "Refresh package indexes"
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 2
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        validate_plan(context.plan)
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["package_indexes_refreshed"] = False
        if not context.plan.software.install_updates:
            return
        if context.values.get("network_online") is False:
            raise StepWarning(
                "Skipped package index refresh because the installer is offline"
            )
        refresh_package_indexes(context, self.runner)

    def verify(self, context: InstallContext) -> None:
        return None

    def cleanup(self, context: InstallContext) -> None:
        return None


@dataclass
class UpgradeSystemStep:
    """Apply upgrades only after a successful index refresh."""

    runner: CommandRunner
    id: str = "upgrade-system"
    title: str = "Install available updates"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 8
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        validate_plan(context.plan)
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["system_upgraded"] = False
        context.values["system_upgrade_downloaded"] = False
        if not context.plan.software.install_updates:
            return
        if context.values.get("network_online") is False:
            raise StepWarning(
                "Skipped system updates because the installer is offline"
            )
        if not context.values.get("package_indexes_refreshed"):
            raise StepWarning(
                "Skipped system updates because package indexes were not refreshed"
            )
        target = _target(context)
        _require_target_command(target, "usr/bin/apt-get")
        command_prefix = (
            "chroot",
            str(target),
            "/usr/bin/env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "--yes",
            "-o",
            "Dpkg::Options::=--force-confold",
        )
        try:
            download = self.runner.run(
                (
                    *command_prefix,
                    "-o",
                    "Acquire::Retries=1",
                    "-o",
                    "Acquire::http::Timeout=15",
                    "-o",
                    "Acquire::https::Timeout=15",
                    "--download-only",
                    "upgrade",
                ),
                check=False,
                timeout=7200,
            )
        except CommandError as error:
            raise StepWarning(
                "Could not download every system update; skipped the upgrade "
                "to preserve the installation media's consistent package set"
            ) from error
        if download.returncode != 0:
            raise StepWarning(
                "Could not download every system update; skipped the upgrade "
                "to preserve the installation media's consistent package set"
            )
        context.values["system_upgrade_downloaded"] = True
        self.runner.run(
            (*command_prefix, "--no-download", "upgrade"),
            timeout=7200,
        )
        context.values["system_upgraded"] = True

    def verify(self, context: InstallContext) -> None:
        if not context.values.get("system_upgraded"):
            return
        target = _target(context)
        self.runner.run(
            ("chroot", str(target), "dpkg", "--audit"),
            timeout=300,
        )
        self.runner.run(
            ("chroot", str(target), "apt-get", "check"),
            timeout=600,
        )

    def cleanup(self, context: InstallContext) -> None:
        return None


@dataclass
class InstallThirdPartyDriversStep:
    """Install hardware-recommended non-free drivers only when requested."""

    runner: CommandRunner
    id: str = "install-third-party-drivers"
    title: str = "Install third-party hardware drivers"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 8
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        validate_plan(context.plan)
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["third_party_drivers_installed"] = False
        if not context.plan.software.install_third_party_drivers:
            return
        if context.values.get("network_online") is False:
            raise StepWarning(
                "Skipped third-party drivers because the installer is offline"
            )
        target = _target(context)
        _require_target_command(target, "usr/bin/ubuntu-drivers")
        result = self.runner.run(
            (
                "chroot",
                str(target),
                "ubuntu-drivers",
                "install",
                "--no-oem",
                "--package-list",
                "/run/anduinos-installer-drivers",
            ),
            check=False,
            timeout=7200,
        )
        if result.returncode != 0:
            audit = self.runner.run(
                ("chroot", str(target), "dpkg", "--audit"),
                check=False,
                timeout=300,
            )
            dependency_check = self.runner.run(
                ("chroot", str(target), "apt-get", "check"),
                check=False,
                timeout=600,
            )
            if (
                audit.returncode != 0
                or audit.stdout.strip()
                or dependency_check.returncode != 0
            ):
                raise CommandError(
                    "Third-party driver installation failed and left an "
                    "inconsistent package state"
                )
            raise StepWarning(
                "Could not download or install the selected third-party "
                "drivers; the base system remains usable"
            )
        context.values["third_party_drivers_installed"] = True

    def verify(self, context: InstallContext) -> None:
        return None

    def cleanup(self, context: InstallContext) -> None:
        return None
