"""Install optional Ubuntu language support for the selected locale."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from languages import language_for_locale, language_pack_packages

from .command import CommandError, CommandRunner
from .software import refresh_package_indexes
from .steps import FailurePolicy, InstallContext, StepWarning


@dataclass
class InstallLanguagePacksStep:
    """Install the selected language's base and GNOME translation packs."""

    runner: CommandRunner
    id: str = "install-language-packs"
    title: str = "Install language support"
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 3
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        language = language_for_locale(context.plan.regional.locale)
        if language is None:
            raise RuntimeError(
                "Selected locale has no maintained language-pack policy"
            )
        packages = language_pack_packages(language)
        context.values["language_pack_packages"] = packages
        context.values["language_packs_installed"] = False

        missing = _missing_packages(target, packages, self.runner)
        if not missing:
            context.values["language_packs_installed"] = True
            context.log(
                f"Language support: {language.language_pack_code} already "
                "installed"
            )
            return

        if context.values.get("network_online") is False:
            raise StepWarning(
                f"Could not install complete {language.native_name} language "
                "support because the installer is offline"
            )
        _require_target_command(target, "usr/bin/apt-get")
        try:
            if not context.values.get("package_indexes_refreshed"):
                refresh_package_indexes(context, self.runner)
        except CommandError as error:
            raise StepWarning(
                f"Could not refresh package indexes for "
                f"{language.native_name} language support"
            ) from error

        result = self.runner.run(
            (
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
                *packages,
            ),
            check=False,
            timeout=3600,
        )
        if result.returncode != 0:
            raise StepWarning(
                f"Could not download or install complete "
                f"{language.native_name} language support"
            )

        remaining = _missing_packages(target, packages, self.runner)
        if remaining:
            raise StepWarning(
                "Language support installation completed without all "
                "required packages: " + ", ".join(remaining)
            )
        context.values["language_packs_installed"] = True
        context.log(
            f"Language support: {language.language_pack_code} installed"
        )

    def verify(self, context: InstallContext) -> None:
        if context.values.get("language_packs_installed") is not True:
            return
        target = _target(context)
        packages = context.values.get("language_pack_packages")
        if not isinstance(packages, tuple):
            raise RuntimeError("Language-pack installation was not recorded")
        missing = _missing_packages(target, packages, self.runner)
        if missing:
            raise RuntimeError(
                "Language-pack verification failed: " + ", ".join(missing)
            )

    def cleanup(self, context: InstallContext) -> None:
        return None


def _missing_packages(
    target: Path,
    packages: tuple[str, ...],
    runner: CommandRunner,
) -> tuple[str, ...]:
    missing = []
    for package in packages:
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
            timeout=30,
        )
        if result.returncode != 0 or result.stdout.strip() != "ii":
            missing.append(package)
    return tuple(missing)


def _require_target_command(target: Path, relative: str) -> None:
    if not (target / relative).is_file():
        raise RuntimeError(f"Target command is missing: /{relative}")


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    if not context.values.get("chroot_environment_ready"):
        raise RuntimeError("Target chroot environment is not ready")
    return target
