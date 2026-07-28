"""Remove packages that exist only to support the Casper live session."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .steps import FailurePolicy, InstallContext


PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?$")
ALWAYS_REMOVE = frozenset({"anduinos-installer-beta"})


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
        for path in _manifest_paths(context):
            if not path.is_file():
                raise RuntimeError(f"Casper manifest is missing: {path}")

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        full_path, desktop_path = _manifest_paths(context)
        full = _read_manifest(full_path)
        desktop = _read_manifest(desktop_path)
        candidates = sorted((full - desktop) | ALWAYS_REMOVE)

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


def _read_manifest(path: Path) -> set[str]:
    packages: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        package = line.split(maxsplit=1)[0]
        if not PACKAGE_RE.fullmatch(package):
            raise RuntimeError(f"Invalid package in Casper manifest: {package!r}")
        packages.add(package)
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

