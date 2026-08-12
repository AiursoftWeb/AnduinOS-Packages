"""Remove the fixed set of packages used only by the Live environment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandRunner
from .model import Filesystem
from .steps import FailurePolicy, InstallContext
from .snapshots_manager import SNAPSHOTS_MANAGER_PACKAGE


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

VMWARE_GUEST_PACKAGES = (
    "open-vm-tools-desktop",
    "open-vm-tools",
    "xserver-xorg-video-vmware",
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
        self.runner.require_commands(("chroot", "systemd-detect-virt"))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        candidates = list(LIVE_ONLY_PACKAGES)
        if context.plan.storage.filesystem is Filesystem.EXT4:
            candidates.append(SNAPSHOTS_MANAGER_PACKAGE)
            context.log(
                "Disk Snapshots Manager cleanup policy: remove it from the ext4 target"
            )
        else:
            context.log(
                "Disk Snapshots Manager cleanup policy: retain it on the Btrfs target"
            )

        virtualization = _detect_virtualization(self.runner)
        context.values["install_virtualization"] = virtualization
        if virtualization == "vmware":
            context.log(
                "VMware guest cleanup policy: retain VMware guest packages"
            )
        elif virtualization is None:
            context.log(
                "VMware guest cleanup policy: virtualization detection was "
                "inconclusive; retain VMware guest packages"
            )
        else:
            candidates.extend(VMWARE_GUEST_PACKAGES)
            context.log(
                "VMware guest cleanup policy: remove VMware guest packages "
                f"from the {virtualization} target"
            )

        installed = tuple(
            package
            for package in candidates
            if _is_installed(self.runner, target, package)
        )
        context.values["live_package_candidates"] = tuple(candidates)
        context.values["live_packages_removed"] = installed
        if not installed:
            context.log("No cleanup candidate packages are installed in the target")
            return

        context.log("Removing target packages: " + ", ".join(installed))
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
        context.log("Removing orphaned target packages and configuration")
        self.runner.run(
            (
                "chroot",
                str(target),
                "/usr/bin/env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "--yes",
                "autoremove",
                "--purge",
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


def _detect_virtualization(runner: CommandRunner) -> str | None:
    try:
        result = runner.run(
            ("systemd-detect-virt", "--vm"),
            check=False,
            timeout=10,
        )
    except CommandError:
        return None
    virtualization = result.stdout.strip().casefold()
    if result.returncode == 0 and virtualization:
        return virtualization
    if result.returncode == 1 and virtualization == "none":
        return "physical"
    return None


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    if not context.values.get("chroot_environment_ready"):
        raise RuntimeError("Target chroot environment is not ready")
    return target
