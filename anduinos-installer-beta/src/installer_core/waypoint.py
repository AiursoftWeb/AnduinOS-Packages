"""Ensure the Btrfs-only AnduinOS Waypoint capability is present."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandRunner
from .software import refresh_package_indexes
from .steps import FailurePolicy, InstallContext, StepWarning


WAYPOINT_PACKAGE = "anduinos-waypoint-gtk"


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    if not context.values.get("chroot_environment_ready"):
        raise RuntimeError("Target chroot environment is not ready")
    return target


def _installed_package_version(
    runner: CommandRunner,
    target: Path,
) -> str | None:
    result = runner.run(
        (
            "chroot",
            str(target),
            "dpkg-query",
            "--show",
            "--showformat=${db:Status-Abbrev}\t${Version}",
            WAYPOINT_PACKAGE,
        ),
        check=False,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.startswith("ii "):
        return None
    return result.stdout[3:].strip() or "unknown"


@dataclass
class EnsureWaypointStep:
    """Retain the copied package or install it from APT on transitional ISOs."""

    runner: CommandRunner
    id: str = "ensure-waypoint"
    title: str = "Ensure Waypoint is available"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 2
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["waypoint_installed"] = False
        context.values["waypoint_source"] = None
        context.values["waypoint_version"] = None
        target = _target(context)
        apt_get = target / "usr/bin/apt-get"
        if not apt_get.is_file():
            raise RuntimeError("Target command is missing: /usr/bin/apt-get")

        online = context.values.get("network_online") is not False
        context.log("Waypoint target policy: required for Btrfs")
        context.log(
            "Waypoint repository fallback: "
            + ("available" if online else "unavailable while offline")
        )

        version = _installed_package_version(self.runner, target)
        if version is not None:
            context.values["waypoint_installed"] = True
            context.values["waypoint_source"] = "copied-system"
            context.values["waypoint_version"] = version
            context.log(
                f"Waypoint package state: installed ({version})"
            )
            context.log("Waypoint package source: copied-system")
            context.log(
                "Retained AnduinOS Waypoint from the copied Live system"
            )
            return

        context.log("Waypoint package state: missing from target")
        if context.values.get("network_online") is False:
            raise StepWarning(
                "The installation media does not contain Waypoint "
                "and the installer is offline; skipped this optional Btrfs "
                "feature"
            )

        if not context.values.get("package_indexes_refreshed"):
            try:
                refresh_package_indexes(context, self.runner)
            except CommandError as error:
                raise StepWarning(
                    "Could not refresh package indexes; skipped the optional "
                    "Waypoint installation"
                ) from error

        command = (
            "chroot",
            str(target),
            "/usr/bin/env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "--yes",
            "--no-install-recommends",
            "-o",
            "Acquire::Retries=1",
            "-o",
            "Acquire::http::Timeout=15",
            "-o",
            "Acquire::https::Timeout=15",
            "install",
            WAYPOINT_PACKAGE,
        )
        result = self.runner.run(command, check=False, timeout=1800)
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
                    "Waypoint installation failed and left an "
                    "inconsistent package state"
                )
            raise StepWarning(
                "Could not download Waypoint; the installed Btrfs "
                "system remains usable"
            )

        context.values["waypoint_installed"] = True
        context.values["waypoint_source"] = "repository"
        version = _installed_package_version(self.runner, target)
        if version is None:
            raise RuntimeError(
                "Waypoint package verification failed after install"
            )
        context.values["waypoint_version"] = version
        context.log(f"Waypoint package state: installed ({version})")
        context.log("Waypoint package source: repository")
        context.log(
            "Installed AnduinOS Waypoint from the signed package "
            "repository"
        )

    def verify(self, context: InstallContext) -> None:
        if not context.values.get("waypoint_installed"):
            return
        target = _target(context)
        version = _installed_package_version(self.runner, target)
        if version is None:
            raise RuntimeError(
                "Waypoint package verification failed"
            )
        context.values["waypoint_version"] = version
        context.log(
            "Waypoint verification: package is installed and the "
            f"target package database is consistent ({version})"
        )
        self.runner.run(
            ("chroot", str(target), "dpkg", "--audit"),
            timeout=300,
        )

    def cleanup(self, context: InstallContext) -> None:
        return None
