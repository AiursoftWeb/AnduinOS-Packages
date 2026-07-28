"""Optional package operations performed inside the isolated target."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandRunner
from .mirrors import restore_original_mirror
from .steps import FailurePolicy, InstallContext
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
        target = _target(context)
        _require_target_command(target, "usr/bin/apt-get")
        command = (
            "chroot",
            str(target),
            "/usr/bin/env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "update",
        )
        result = self.runner.run(command, check=False, timeout=1800)
        if result.returncode != 0:
            if restore_original_mirror(context):
                context.log(
                    "Selected mirror failed apt update; restored original sources"
                )
                result = self.runner.run(command, check=False, timeout=1800)
            if result.returncode != 0:
                raise CommandError(
                    "Could not refresh package indexes; continuing with the "
                    "installation media's package set"
                )
        context.values["package_indexes_refreshed"] = True

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
        if not context.plan.software.install_updates:
            return
        if not context.values.get("package_indexes_refreshed"):
            context.log(
                "[upgrade-system] skipped because package indexes were not refreshed"
            )
            return
        target = _target(context)
        _require_target_command(target, "usr/bin/apt-get")
        self.runner.run(
            (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "--yes",
                "-o",
                "Dpkg::Options::=--force-confold",
                "upgrade",
            ),
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
        target = _target(context)
        _require_target_command(target, "usr/bin/ubuntu-drivers")
        self.runner.run(
            (
                "chroot",
                str(target),
                "ubuntu-drivers",
                "install",
                "--no-oem",
                "--package-list",
                "/run/anduinos-installer-drivers",
            ),
            timeout=7200,
        )
        context.values["third_party_drivers_installed"] = True

    def verify(self, context: InstallContext) -> None:
        return None

    def cleanup(self, context: InstallContext) -> None:
        return None
