"""Ensure the Btrfs-only AnduinOS Timeback Machine capability is present."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandRunner
from .software import refresh_package_indexes
from .steps import FailurePolicy, InstallContext, StepWarning


TIMEBACK_PACKAGE = "anduinos-timeback-machine"


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
            TIMEBACK_PACKAGE,
        ),
        check=False,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.startswith("ii "):
        return None
    return result.stdout[3:].strip() or "unknown"


@dataclass
class EnsureTimebackMachineStep:
    """Retain the ISO payload or install it from APT on transitional ISOs."""

    runner: CommandRunner
    id: str = "ensure-timeback-machine"
    title: str = "Ensure Timeback Machine is available"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 2
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["timeback_machine_installed"] = False
        context.values["timeback_machine_source"] = None
        context.values["timeback_machine_version"] = None
        target = _target(context)
        apt_get = target / "usr/bin/apt-get"
        if not apt_get.is_file():
            raise RuntimeError("Target command is missing: /usr/bin/apt-get")

        media_payload = bool(
            context.values.get("timeback_payload_in_live_image")
        )
        online = context.values.get("network_online") is not False
        context.log("Timeback Machine target policy: required for Btrfs")
        context.log(
            "Timeback Machine installation-media payload: "
            + ("present" if media_payload else "absent")
        )
        context.log(
            "Timeback Machine repository fallback: "
            + ("available" if online else "unavailable while offline")
        )

        version = _installed_package_version(self.runner, target)
        if version is not None:
            source = (
                "installation-media"
                if media_payload
                else "copied-system"
            )
            context.values["timeback_machine_installed"] = True
            context.values["timeback_machine_source"] = source
            context.values["timeback_machine_version"] = version
            context.log(
                f"Timeback Machine package state: installed ({version})"
            )
            context.log(f"Timeback Machine package source: {source}")
            if source == "installation-media":
                context.log(
                    "Retained AnduinOS Timeback Machine from the "
                    "installation media"
                )
            else:
                context.log(
                    "AnduinOS Timeback Machine is already installed in the "
                    "copied system"
                )
            return

        context.log("Timeback Machine package state: missing from target")
        if context.values.get("network_online") is False:
            raise StepWarning(
                "The installation media does not contain Timeback Machine "
                "and the installer is offline; skipped this optional Btrfs "
                "feature"
            )

        if not context.values.get("package_indexes_refreshed"):
            try:
                refresh_package_indexes(context, self.runner)
            except CommandError as error:
                raise StepWarning(
                    "Could not refresh package indexes; skipped the optional "
                    "Timeback Machine installation"
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
            TIMEBACK_PACKAGE,
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
                    "Timeback Machine installation failed and left an "
                    "inconsistent package state"
                )
            raise StepWarning(
                "Could not download Timeback Machine; the installed Btrfs "
                "system remains usable"
            )

        context.values["timeback_machine_installed"] = True
        context.values["timeback_machine_source"] = "repository"
        version = _installed_package_version(self.runner, target)
        if version is None:
            raise RuntimeError(
                "Timeback Machine package verification failed after install"
            )
        context.values["timeback_machine_version"] = version
        context.log(f"Timeback Machine package state: installed ({version})")
        context.log("Timeback Machine package source: repository")
        context.log(
            "Installed AnduinOS Timeback Machine from the signed package "
            "repository"
        )

    def verify(self, context: InstallContext) -> None:
        if not context.values.get("timeback_machine_installed"):
            return
        target = _target(context)
        version = _installed_package_version(self.runner, target)
        if version is None:
            raise RuntimeError(
                "Timeback Machine package verification failed"
            )
        context.values["timeback_machine_version"] = version
        context.log(
            "Timeback Machine verification: package is installed and the "
            f"target package database is consistent ({version})"
        )
        self.runner.run(
            ("chroot", str(target), "dpkg", "--audit"),
            timeout=300,
        )

    def cleanup(self, context: InstallContext) -> None:
        return None
