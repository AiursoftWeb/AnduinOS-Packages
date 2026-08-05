"""Remove the fixed set of packages used only by the Live environment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .model import Filesystem
from .steps import FailurePolicy, InstallContext
from .waypoint import WAYPOINT_PACKAGE


# This is deliberately installer-owned policy. It must not be derived from
# Casper's historical filesystem.manifest-desktop convention.
LIVE_ONLY_PACKAGES = (
    "casper",
    "discover",
    "laptop-detect",
    "os-prober",
    "gparted",
    "anduinos-installer-beta",
    "anduinos-live-settings",
)


@dataclass
class RemoveLivePackagesStep:
    runner: CommandRunner
    id: str = "remove-live-packages"
    title: str = "Remove live-session components"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 4
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        candidates = list(LIVE_ONLY_PACKAGES)
        if context.plan.storage.filesystem is Filesystem.EXT4:
            candidates.append(WAYPOINT_PACKAGE)
            context.log(
                "Waypoint cleanup policy: remove it from the ext4 target"
            )
        else:
            context.log(
                "Waypoint cleanup policy: retain it on the Btrfs target"
            )

        installed = tuple(
            package
            for package in candidates
            if _is_installed(self.runner, target, package)
        )
        context.values["live_package_candidates"] = tuple(candidates)
        context.values["live_packages_removed"] = installed
        if not installed:
            context.log("No Live-only packages are installed in the target")
            return

        context.log("Removing Live-only packages: " + ", ".join(installed))
        self.runner.run(
            (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "--yes",
                "purge",
                *installed,
            ),
            timeout=1800,
        )

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        candidates = context.values.get("live_package_candidates")
        if not isinstance(candidates, tuple):
            raise RuntimeError("Live-package cleanup did not execute")
        remaining = tuple(
            package
            for package in candidates
            if _is_installed(self.runner, target, package)
        )
        if remaining:
            raise RuntimeError(
                "Live-only packages remain installed: " + ", ".join(remaining)
            )
        self.runner.run(
            ("chroot", str(target), "dpkg", "--audit"), timeout=60
        )

    def cleanup(self, context: InstallContext) -> None:
        return None


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
