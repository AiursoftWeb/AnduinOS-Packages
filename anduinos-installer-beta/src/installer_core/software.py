"""Optional package operations performed inside the isolated target."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .command import CommandError, CommandRunner
from .mirrors import restore_original_mirror
from .steps import (
    FailurePolicy,
    InstallContext,
    StepSkipped,
    StepWarning,
)


MULTIMEDIA_CODECS_PACKAGE = "anduinos-multimedia-codecs"


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


def refresh_package_indexes(
    context: InstallContext,
    runner: CommandRunner,
) -> None:
    """Refresh target indexes with mirror rollback for package consumers."""

    context.values["package_indexes_refreshed"] = False
    target = _target(context)
    _require_target_command(target, "usr/bin/apt-get")
    command = (
        "chroot",
        str(target),
        "/usr/bin/env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "-o",
        "Acquire::Retries=1",
        "-o",
        "Acquire::http::Timeout=15",
        "-o",
        "Acquire::https::Timeout=15",
        "update",
    )
    result = runner.run(command, check=False, timeout=1800)
    if result.returncode != 0:
        if restore_original_mirror(context):
            context.log(
                "Selected mirror failed apt update; restored original sources"
            )
            result = runner.run(command, check=False, timeout=1800)
        if result.returncode != 0:
            raise CommandError(
                "Could not refresh package indexes; continuing with the "
                "installation media's package set"
            )
    context.values["package_indexes_refreshed"] = True


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
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        if not context.plan.software.install_updates:
            return
        if context.values.get("package_indexes_refreshed"):
            context.log("Reusing package indexes refreshed for an earlier step")
            return
        context.values["package_indexes_refreshed"] = False
        if context.values.get("network_online") is False:
            raise StepWarning(
                "Skipped package index refresh because the installer is offline"
            )
        refresh_package_indexes(context, self.runner)

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
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["system_upgraded"] = False
        context.values["system_upgrade_downloaded"] = False
        if not context.plan.software.install_updates:
            return
        if context.values.get("network_online") is False:
            raise StepWarning(
                "Skipped system updates because the installer is offline"
            )
        if not context.values.get("package_indexes_refreshed"):
            raise StepWarning(
                "Skipped system updates because package indexes were not refreshed"
            )
        target = _target(context)
        _require_target_command(target, "usr/bin/apt-get")
        command_prefix = (
            "chroot",
            str(target),
            "/usr/bin/env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "--yes",
            "-o",
            "Dpkg::Options::=--force-confold",
        )
        try:
            download = self.runner.run(
                (
                    *command_prefix,
                    "-o",
                    "Acquire::Retries=1",
                    "-o",
                    "Acquire::http::Timeout=15",
                    "-o",
                    "Acquire::https::Timeout=15",
                    "--download-only",
                    "upgrade",
                ),
                check=False,
                timeout=7200,
            )
        except CommandError as error:
            raise StepWarning(
                "Could not download every system update; skipped the upgrade "
                "to preserve the installation media's consistent package set"
            ) from error
        if download.returncode != 0:
            raise StepWarning(
                "Could not download every system update; skipped the upgrade "
                "to preserve the installation media's consistent package set"
            )
        context.values["system_upgrade_downloaded"] = True
        self.runner.run(
            (*command_prefix, "--no-download", "upgrade"),
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


def _package_is_installed(
    target: Path,
    package: str,
    runner: CommandRunner,
) -> bool:
    result = runner.run(
        (
            "chroot",
            str(target),
            "dpkg-query",
            "-W",
            "-f=${db:Status-Abbrev}",
            package,
        ),
        check=False,
        timeout=60,
    )
    return result.returncode == 0 and result.stdout.strip() == "ii"


def _package_state_is_consistent(
    target: Path,
    runner: CommandRunner,
) -> bool:
    audit = runner.run(
        ("chroot", str(target), "dpkg", "--audit"),
        check=False,
        timeout=300,
    )
    dependency_check = runner.run(
        ("chroot", str(target), "apt-get", "check"),
        check=False,
        timeout=600,
    )
    return (
        audit.returncode == 0
        and not audit.stdout.strip()
        and dependency_check.returncode == 0
    )


@dataclass
class InstallMultimediaCodecsStep:
    """Install the selected extended-format metapackage in the target."""

    runner: CommandRunner
    id: str = "install-multimedia-codecs"
    title: str = "Install extended multimedia format support"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 3
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["multimedia_codecs_installed"] = False
        if not context.plan.software.install_multimedia_codecs:
            raise StepSkipped(
                "Skipped extended multimedia formats because they were not "
                "selected"
            )

        target = _target(context)
        if _package_is_installed(
            target, MULTIMEDIA_CODECS_PACKAGE, self.runner
        ):
            context.values["multimedia_codecs_installed"] = True
            context.log(
                "Extended multimedia format support is already installed"
            )
            return

        if context.values.get("network_online") is False:
            raise StepWarning(
                "Skipped extended multimedia formats because the installer "
                "is offline; everyday playback remains available"
            )

        _require_target_command(target, "usr/bin/apt-get")
        try:
            if not context.values.get("package_indexes_refreshed"):
                refresh_package_indexes(context, self.runner)
        except CommandError as error:
            raise StepWarning(
                "Could not refresh package indexes for extended multimedia "
                "formats; everyday playback remains available"
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
                MULTIMEDIA_CODECS_PACKAGE,
            ),
            check=False,
            timeout=3600,
        )
        if result.returncode != 0:
            if not _package_state_is_consistent(target, self.runner):
                raise CommandError(
                    "Extended multimedia format installation failed and "
                    "left an inconsistent package state"
                )
            raise StepWarning(
                "Could not download or install extended multimedia formats; "
                "everyday playback remains available"
            )

        context.values["multimedia_codecs_installed"] = True
        context.log(
            "Installed extended multimedia format support from "
            f"{MULTIMEDIA_CODECS_PACKAGE}"
        )

    def verify(self, context: InstallContext) -> None:
        if not context.plan.software.install_multimedia_codecs:
            return
        target = _target(context)
        if context.values.get("multimedia_codecs_installed") is not True:
            raise RuntimeError(
                "Extended multimedia format installation was not recorded"
            )
        if not _package_is_installed(
            target, MULTIMEDIA_CODECS_PACKAGE, self.runner
        ):
            raise RuntimeError(
                "Extended multimedia format metapackage is not installed"
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
        context.validate_plan()
        self.runner.require_commands(("chroot",))

    def execute(self, context: InstallContext) -> None:
        context.values["third_party_drivers_installed"] = False
        if not context.plan.software.install_third_party_drivers:
            return
        if context.values.get("network_online") is False:
            raise StepWarning(
                "Skipped third-party drivers because the installer is offline"
            )
        target = _target(context)
        _require_target_command(target, "usr/bin/ubuntu-drivers")
        # Platform metapackages can bring in a different kernel flavour during
        # the first transaction. Resolve once more against that installed state,
        # never against uname -r (which is the Live kernel inside a chroot).
        for attempt in range(2):
            kernels = _installed_kernel_versions(target)
            result = self.runner.run(
                (
                    "chroot",
                    str(target),
                    "ubuntu-drivers",
                    "install",
                    "--no-oem",
                    "--package-list",
                    "/run/anduinos-installer-drivers",
                ),
                check=False,
                timeout=7200,
            )
            # Some ubuntu-drivers versions discard apt-get's nonzero return
            # value. A successful outer command is not a package health check.
            _verify_driver_package_state(self.runner, target)
            if result.returncode != 0:
                if attempt or _installed_kernel_versions(target) != kernels:
                    raise CommandError(
                        "Could not complete driver installation for the changed target kernels"
                    )
                raise StepWarning(
                    "Could not download or install the selected third-party "
                    "drivers; the base system remains usable"
                )
            if _installed_kernel_versions(target) == kernels:
                break
            if attempt == 1:
                raise CommandError(
                    "Driver installation kept changing the target kernels; "
                    "could not verify a stable driver selection"
                )
            context.log("Target kernels changed; resolving hardware drivers again")
        context.values["third_party_drivers_installed"] = True

    def verify(self, context: InstallContext) -> None:
        if context.values.get("third_party_drivers_installed"):
            _verify_driver_package_state(self.runner, _target(context))

    def cleanup(self, context: InstallContext) -> None:
        return None


def _installed_kernel_versions(target: Path) -> frozenset[str]:
    return frozenset(
        path.name.removeprefix("vmlinuz-")
        for path in (target / "boot").glob("vmlinuz-*")
        if path.is_file()
    )


def _verify_driver_package_state(runner: CommandRunner, target: Path) -> None:
    audit = runner.run(
        ("chroot", str(target), "dpkg", "--audit"),
        check=False,
        timeout=300,
    )
    dependency_check = runner.run(
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
            "Third-party driver installation left an inconsistent package state"
        )


def verify_driver_boot_payload(
    context: InstallContext, runner: CommandRunner, grub_config: str
) -> None:
    """Check the final boot target, after DKMS and bootloader generation."""
    if not context.values.get("third_party_drivers_installed"):
        return
    target = _target(context)
    _verify_driver_package_state(runner, target)
    _require_target_command(target, "usr/libexec/anduinos-dracut-verify")
    runner.run(
        ("chroot", str(target), "/usr/libexec/anduinos-dracut-verify", "--verify-default"),
        timeout=300,
    )
    packages = runner.run(
        (
            "chroot", str(target), "dpkg-query", "-W",
            "-f=${binary:Package}\t${db:Status-Abbrev}\n",
        ),
        timeout=60,
        log_output=False,
    )
    has_nvidia_driver = any(
        name.startswith("nvidia-driver-") and status == "ii "
        for line in packages.stdout.splitlines()
        for name, separator, status in (line.partition("\t"),)
        if separator
    )
    if not has_nvidia_driver:
        return
    # The generated first Linux entry is the installer's default. Use its ABI,
    # not the newest generic kernel or the kernel running the Live installer.
    match = re.search(
        r"^\s*linux(?:efi|16)?\s+\S*/vmlinuz-([A-Za-z0-9._+-]+)(?:\s|$)",
        grub_config,
        re.MULTILINE,
    )
    if not match or match[1] not in _installed_kernel_versions(target):
        raise CommandError("Could not identify the target kernel for NVIDIA verification")
    version = match[1]
    for module in ("nvidia", "nvidia_modeset", "nvidia_drm", "nvidia_uvm"):
        result = runner.run(
            (
                "chroot",
                str(target),
                "modinfo", "-k", version, "-F", "vermagic", module,
            ),
            check=False,
            timeout=60,
        )
        vermagic = result.stdout.split()
        if result.returncode != 0 or not vermagic or vermagic[0] != version:
            raise CommandError(
                f"NVIDIA module {module} is missing or incompatible with target kernel {version}"
            )
