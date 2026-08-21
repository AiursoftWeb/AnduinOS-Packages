"""Ensure the Btrfs-only Disk Snapshots Manager capability is present."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandRunner
from .software import refresh_package_indexes
from .steps import FailurePolicy, InstallContext, StepWarning


SNAPSHOTS_MANAGER_PACKAGE = "anduinos-btrfs-snapshots-manager"


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
            SNAPSHOTS_MANAGER_PACKAGE,
        ),
        check=False,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.startswith("ii "):
        return None
    return result.stdout[3:].strip() or "unknown"


@dataclass
class EnsureSnapshotsManagerStep:
    """Retain the copied package or install it from APT on transitional ISOs."""

    runner: CommandRunner
    id: str = "ensure-snapshots-manager"
    title: str = "Ensure Disk Snapshots Manager is available"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 2
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["snapshots_manager_installed"] = False
        context.values["snapshots_manager_source"] = None
        context.values["snapshots_manager_version"] = None
        target = _target(context)
        apt_get = target / "usr/bin/apt-get"
        if not apt_get.is_file():
            raise RuntimeError("Target command is missing: /usr/bin/apt-get")

        online = context.values.get("network_online") is not False
        context.log("Disk Snapshots Manager target policy: required for Btrfs")
        context.log(
            "Disk Snapshots Manager repository fallback: "
            + ("available" if online else "unavailable while offline")
        )

        version = _installed_package_version(self.runner, target)
        if version is not None:
            context.values["snapshots_manager_installed"] = True
            context.values["snapshots_manager_source"] = "copied-system"
            context.values["snapshots_manager_version"] = version
            context.log(
                f"Disk Snapshots Manager package state: installed ({version})"
            )
            context.log("Disk Snapshots Manager package source: copied-system")
            context.log(
                "Retained Disk Snapshots Manager from the copied Live system"
            )
            return

        context.log("Disk Snapshots Manager package state: missing from target")
        if context.values.get("network_online") is False:
            raise StepWarning(
                "The installation media does not contain Disk Snapshots Manager "
                "and the installer is offline; skipped this optional Btrfs "
                "feature"
            )

        if not context.values.get("package_indexes_refreshed"):
            try:
                refresh_package_indexes(context, self.runner)
            except CommandError as error:
                raise StepWarning(
                    "Could not refresh package indexes; skipped the optional "
                    "Disk Snapshots Manager installation"
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
            SNAPSHOTS_MANAGER_PACKAGE,
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
                    "Disk Snapshots Manager installation failed and left an "
                    "inconsistent package state"
                )
            raise StepWarning(
                "Could not download Disk Snapshots Manager; the installed Btrfs "
                "system remains usable"
            )

        context.values["snapshots_manager_installed"] = True
        context.values["snapshots_manager_source"] = "repository"
        version = _installed_package_version(self.runner, target)
        if version is None:
            raise RuntimeError(
                "Disk Snapshots Manager package verification failed after install"
            )
        context.values["snapshots_manager_version"] = version
        context.log(f"Disk Snapshots Manager package state: installed ({version})")
        context.log("Disk Snapshots Manager package source: repository")
        context.log(
            "Installed Disk Snapshots Manager from the signed package "
            "repository"
        )

    def verify(self, context: InstallContext) -> None:
        if not context.values.get("snapshots_manager_installed"):
            return
        target = _target(context)
        version = _installed_package_version(self.runner, target)
        if version is None:
            raise RuntimeError(
                "Disk Snapshots Manager package verification failed"
            )
        context.values["snapshots_manager_version"] = version
        context.log(
            "Disk Snapshots Manager verification: package is installed and the "
            f"target package database is consistent ({version})"
        )
        self.runner.run(
            ("chroot", str(target), "dpkg", "--audit"),
            timeout=300,
        )

    def cleanup(self, context: InstallContext) -> None:
        return None
