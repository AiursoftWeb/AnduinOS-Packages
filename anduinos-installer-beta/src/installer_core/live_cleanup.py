"""Remove packages that exist only to support the Casper live session."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .model import Filesystem
from .steps import FailurePolicy, InstallContext


PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?$")
VERSION_RE = re.compile(r"^[A-Za-z0-9.+:~\-]+$")
ALWAYS_REMOVE = frozenset({"anduinos-installer-beta"})
CONDITIONAL_LIVE_PACKAGES = frozenset({"anduinos-waypoint-gtk"})
@dataclass
class CleanupLiveSystemStep:
    runner: CommandRunner
    id: str = "cleanup-live-system"
    title: str = "Remove live-session packages"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 4
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        self.runner.require_commands(("chroot",))
        full_path, desktop_path = _manifest_paths(context)
        for path in (full_path, desktop_path):
            if not path.is_file():
                raise RuntimeError(f"Casper manifest is missing: {path}")
        full = _read_manifest(full_path)
        desktop = _read_manifest(desktop_path)
        unexpected = sorted(desktop.keys() - full.keys())
        if unexpected:
            raise RuntimeError(
                "Desktop manifest contains packages absent from the full "
                f"manifest: {', '.join(unexpected)}"
            )
        changed_versions = sorted(
            package
            for package in desktop.keys() & full.keys()
            if desktop[package] != full[package]
        )
        if changed_versions:
            raise RuntimeError(
                "Casper manifests disagree on package versions: "
                + ", ".join(changed_versions)
            )
        leaked = sorted(ALWAYS_REMOVE & desktop.keys())
        if leaked:
            raise RuntimeError(
                "Live-only packages leaked into the desktop manifest: "
                + ", ".join(leaked)
            )
        conditional_leaked = sorted(
            CONDITIONAL_LIVE_PACKAGES & desktop.keys()
        )
        if conditional_leaked:
            raise RuntimeError(
                "Conditional live packages leaked into the desktop manifest: "
                + ", ".join(conditional_leaked)
            )
        context.values["casper_full_manifest"] = full
        context.values["casper_desktop_manifest"] = desktop
        context.values["waypoint_payload_in_live_image"] = (
            "anduinos-waypoint-gtk" in full
        )
        payload_version = full.get("anduinos-waypoint-gtk")
        context.values["waypoint_payload_version"] = payload_version
        context.log(
            "Waypoint installation-media payload: "
            + (
                f"present ({payload_version})"
                if payload_version is not None
                else "absent"
            )
        )
        if context.plan.storage.filesystem is Filesystem.BTRFS:
            context.log(
                "Waypoint cleanup policy: retain the payload for "
                "the Btrfs target"
            )
        else:
            context.log(
                "Waypoint cleanup policy: purge the live payload "
                "from the ext4 target"
            )

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        full = context.values.get("casper_full_manifest")
        desktop = context.values.get("casper_desktop_manifest")
        if not isinstance(full, dict) or not isinstance(desktop, dict):
            # Preserve direct step use in diagnostics while keeping production
            # execution tied to the manifests validated before disk changes.
            full_path, desktop_path = _manifest_paths(context)
            full = _read_manifest(full_path)
            desktop = _read_manifest(desktop_path)
        candidates = (full.keys() - desktop.keys()) | ALWAYS_REMOVE
        if context.plan.storage.filesystem is Filesystem.BTRFS:
            candidates -= CONDITIONAL_LIVE_PACKAGES
            context.log(
                "Waypoint cleanup decision: excluded from the "
                "live-package purge set"
            )
        elif "anduinos-waypoint-gtk" in full:
            context.log(
                "Waypoint cleanup decision: included in the "
                "live-package purge set"
            )
        candidates = sorted(candidates)

        installed: list[str] = []
        for package in candidates:
            result = self.runner.run(
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
            if result.returncode == 0 and result.stdout.startswith("ii "):
                installed.append(package)

        context.values["live_packages_removed"] = tuple(installed)
        if installed:
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
        if "anduinos-waypoint-gtk" in installed:
            context.log(
                "Waypoint cleanup result: removed from the ext4 "
                "target"
            )
    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        for package in context.values.get("live_packages_removed", ()):
            result = self.runner.run(
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
            if result.returncode == 0 and result.stdout.startswith("ii "):
                raise RuntimeError(f"Live package remains installed: {package}")
        self.runner.run(
            ("chroot", str(target), "dpkg", "--audit"), timeout=60
        )

    def cleanup(self, context: InstallContext) -> None:
        return None


def _read_manifest(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 2:
            raise RuntimeError(
                f"Invalid Casper manifest line {path}:{line_number}"
            )
        package, version = fields
        if not PACKAGE_RE.fullmatch(package):
            raise RuntimeError(f"Invalid package in Casper manifest: {package!r}")
        if not VERSION_RE.fullmatch(version):
            raise RuntimeError(
                f"Invalid version in Casper manifest: {version!r}"
            )
        if package in packages:
            raise RuntimeError(
                f"Duplicate package in Casper manifest: {package!r}"
            )
        packages[package] = version
    return packages


def _manifest_paths(context: InstallContext) -> tuple[Path, Path]:
    return (
        Path(context.plan.source.manifest_path),
        Path(context.plan.source.desktop_manifest_path),
    )


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target
