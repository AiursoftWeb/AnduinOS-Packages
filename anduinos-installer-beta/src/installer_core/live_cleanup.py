"""Remove the fixed set of packages used only by the Live environment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandError, CommandRunner
from .model import Architecture, Filesystem
from .steps import FailurePolicy, InstallContext
from .snapshots_manager import SNAPSHOTS_MANAGER_PACKAGE


# This is deliberately installer-owned policy. It must not be derived from
# a historical Live-boot filesystem.manifest-desktop convention.
LIVE_ONLY_PACKAGES = (
    "anduinos-live-layers",
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

# These packages enter the copied filesystem through Live-only composition,
# but are capabilities of the installed desktop. Mark them as explicitly
# retained before purging the Live bridge and running autoremove.
PERSISTENT_TARGET_PACKAGES = ("openssh-server",)

# These tools are installed as dependencies of the removable installer, but
# remain essential administration and repair tools for their selected root
# filesystem after the installer package is purged and autoremove runs.
ADVANCED_FILESYSTEM_TOOL_PACKAGES = {
    Filesystem.XFS: "xfsprogs",
    Filesystem.F2FS: "f2fs-tools",
}

# This is a post-autoremove composition contract, not an apt-mark exception.
# The persistent anduinos-core-system metapackage owns these packages so boot
# maintenance remains available after the removable Live installer is purged.
REQUIRED_BOOT_PACKAGES = {
    Architecture.AMD64: (
        "anduinos-core-system",
        "dracut",
        "dracut-core",
        "dracut-install",
        "grub-common",
        "grub2-common",
        "grub-pc-bin",
        "grub-efi-amd64-bin",
        "grub-efi-amd64-signed",
        "shim-signed",
    ),
    Architecture.ARM64: (
        "anduinos-core-system",
        "dracut",
        "dracut-core",
        "dracut-install",
        "grub-common",
        "grub2-common",
        "grub-efi-arm64-bin",
        "grub-efi-arm64-signed",
        "shim-signed",
    ),
}


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
        retained = tuple(
            package
            for package in PERSISTENT_TARGET_PACKAGES
            if _is_installed(self.runner, target, package)
        )
        filesystem_tool = ADVANCED_FILESYSTEM_TOOL_PACKAGES.get(
            context.plan.storage.filesystem
        )
        if filesystem_tool:
            if not _is_installed(self.runner, target, filesystem_tool):
                raise RuntimeError(
                    "Selected root filesystem tools are missing: "
                    + filesystem_tool
                )
            retained += (filesystem_tool,)
        if (
            context.plan.storage.filesystem is Filesystem.BTRFS
            and _is_installed(self.runner, target, SNAPSHOTS_MANAGER_PACKAGE)
        ):
            # The ISO deliberately carries this package for offline Btrfs
            # installations. Do not rely on the copied Live system's APT mark:
            # make the selected-filesystem capability explicit before the
            # installer package and its orphaned dependencies are removed.
            retained += (SNAPSHOTS_MANAGER_PACKAGE,)
        for package in retained:
            self.runner.run(
                ("chroot", str(target), "apt-mark", "manual", package),
                timeout=30,
            )
        context.values["persistent_target_packages"] = retained
        if retained:
            context.log(
                "Retaining installed-system packages: "
                + ", ".join(retained)
            )

        candidates = list(LIVE_ONLY_PACKAGES)
        if context.plan.storage.filesystem is not Filesystem.BTRFS:
            candidates.append(SNAPSHOTS_MANAGER_PACKAGE)
            context.log(
                "Disk Snapshots Manager cleanup policy: remove it from the "
                f"{context.plan.storage.filesystem.value} target"
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
        retained = context.values.get("persistent_target_packages")
        if not isinstance(retained, tuple):
            raise RuntimeError("Persistent-package retention did not execute")
        missing = tuple(
            package
            for package in retained
            if not _is_installed(self.runner, target, package)
        )
        if missing:
            raise RuntimeError(
                "Installed-system packages were removed: " + ", ".join(missing)
            )
        required_boot_packages = REQUIRED_BOOT_PACKAGES[
            context.plan.platform.architecture
        ]
        missing_boot_packages = tuple(
            package
            for package in required_boot_packages
            if not _is_installed(self.runner, target, package)
        )
        if missing_boot_packages:
            raise RuntimeError(
                "Installed-system boot packages are missing after cleanup: "
                + ", ".join(missing_boot_packages)
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
