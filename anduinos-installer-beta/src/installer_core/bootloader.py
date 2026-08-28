"""Install and verify the unsigned GRUB foundation for Milestone 3C."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .boot_commands import build_boot_commands
from .command import CommandRunner
from .esp import (
    EspReuseInspection,
    verify_nvram_entry,
    verify_preserved_esp_tree,
)
from .execution_boundaries import emit_boundary
from .model import Architecture, Filesystem, InstallMode
from .steps import FailurePolicy, InstallContext
from .storage_planning import (
    GuidedCoexistenceExecutionPlan,
    ManualStorageExecutionPlan,
)


GRUB_PLATFORM_MODULES = {
    "i386-pc": Path("usr/lib/grub/i386-pc/modinfo.sh"),
    "x86_64-efi": Path("usr/lib/grub/x86_64-efi/modinfo.sh"),
    "arm64-efi": Path("usr/lib/grub/arm64-efi/modinfo.sh"),
}

GRUB_ADVANCED_FILESYSTEM_MODULES = {
    Filesystem.XFS: "xfs.mod",
    Filesystem.F2FS: "f2fs.mod",
}

INITRD_ADVANCED_FILESYSTEM_MODULES = {
    Filesystem.XFS: "kernel/fs/xfs/xfs.ko",
    Filesystem.F2FS: "kernel/fs/f2fs/f2fs.ko",
}


@dataclass
class InstallBootloaderStep:
    runner: CommandRunner
    id: str = "install-bootloader"
    title: str = "Install kernel and bootloader"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 8
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        # Target files do not exist yet: all preflight checks intentionally run
        # before partitioning. Validate only inputs available at that boundary.
        context.validate_plan()

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        required = (
            target / "usr/sbin/grub-install",
            target / "usr/sbin/update-grub",
            target / "usr/bin/dracut",
            target / "usr/bin/lsinitrd",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError(
                "Target bootloader tools are missing: " + ", ".join(missing)
            )
        if not (target / "boot/efi").is_dir():
            raise RuntimeError("EFI System Partition is not mounted")
        if not context.values.get("target_efi_mounted"):
            raise RuntimeError("EFI mount state is not active")
        guided = context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
        manual = context.plan.storage.mode is InstallMode.MANUAL
        vendor_only = guided or manual
        if vendor_only:
            execution_key = (
                "guided_storage_execution_plan"
                if guided
                else "manual_storage_execution_plan"
            )
            storage_execution = context.values.get(execution_key)
            if not isinstance(
                storage_execution,
                (GuidedCoexistenceExecutionPlan, ManualStorageExecutionPlan),
            ):
                raise RuntimeError("Vendor-only boot command plan is missing")
            commands = storage_execution.boot_commands
            installs = (commands.install,)
        else:
            commands = build_boot_commands(context.plan, str(target))
            installs = commands.installs
        context.values["boot_command_plan"] = commands
        _verify_grub_platform_modules(target, installs)
        _verify_grub_filesystem_modules(
            target,
            installs,
            context.plan.storage.filesystem,
        )
        _verify_grub_install_options(self.runner, target, installs)
        devices = context.values.get("partition_devices", {})
        context.log(
            "Bootloader target disk: "
            f"{context.plan.storage.disk.path} (selected disk only)"
        )
        if not vendor_only and commands.bios_required:
            context.log(
                "Installing Legacy BIOS GRUB to "
                f"{context.plan.storage.disk.path}"
            )
        context.log(
            "Installing UEFI bootloader to "
            f"{devices.get('efi-system', 'the selected disk ESP')} "
            "mounted at /boot/efi"
        )
        if vendor_only:
            context.log(
                "Only EFI/AnduinOS may change on the selected EFI System "
                "Partition"
            )
            context.log(
                "Creating and verifying an AnduinOS UEFI Boot#### entry"
            )
        else:
            context.log("UEFI Boot#### entries will not be modified")
            context.log(
                "Other disks and Windows EFI boot files will not be modified"
            )
        self.runner.run(commands.initrd, timeout=1200)
        prefix = "guided" if guided else "manual"
        for command in installs:
            if vendor_only:
                emit_boundary(context, f"{prefix}-boot-files", "before")
            self.runner.run(command, timeout=300)
            if vendor_only:
                emit_boundary(context, f"{prefix}-boot-files", "after")
        self.runner.run(commands.configure, timeout=300)
        if vendor_only:
            emit_boundary(context, f"{prefix}-nvram", "before")
            self.runner.run(commands.nvram_create, timeout=30)
            _verify_vendor_nvram(self.runner, context, commands)
            emit_boundary(context, f"{prefix}-nvram", "after")

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        commands = context.values.get("boot_command_plan")
        if commands is None:
            raise RuntimeError("Boot command plan is missing")

        kernels = {
            path.name.removeprefix("vmlinuz-")
            for path in (target / "boot").glob("vmlinuz-*")
            if path.is_file()
        }
        initrds = {
            path.name.removeprefix("initrd.img-")
            for path in (target / "boot").glob("initrd.img-*")
            if path.is_file()
        }
        matching_versions = kernels.intersection(initrds)
        if not kernels or not matching_versions:
            raise RuntimeError("No kernel has a matching Dracut initrd")

        forbidden_live_modules = {
            "dmsquash-live",
            "dmsquash-live-autooverlay",
            "livenet",
            "anduinos-live-layers",
        }
        for version in sorted(matching_versions):
            modules = set(
                self.runner.run(
                    (
                        "chroot",
                        str(target),
                        "lsinitrd",
                        "-m",
                        f"/boot/initrd.img-{version}",
                    ),
                    timeout=60,
                ).stdout.splitlines()
            )
            unexpected = sorted(forbidden_live_modules.intersection(modules))
            if unexpected:
                raise RuntimeError(
                    "Installed-system initrd contains Live modules: "
                    + ", ".join(unexpected)
                )
            if (
                context.plan.storage.filesystem is Filesystem.BTRFS
                and "anduinos-btrfs-snapshots-manager" not in modules
            ):
                raise RuntimeError(
                    "Btrfs target initrd is missing Disk Snapshots Manager recovery"
                )
            root_module = INITRD_ADVANCED_FILESYSTEM_MODULES.get(
                context.plan.storage.filesystem
            )
            if root_module:
                initrd_contents = self.runner.run(
                    (
                        "chroot",
                        str(target),
                        "lsinitrd",
                        f"/boot/initrd.img-{version}",
                    ),
                    timeout=60,
                ).stdout
                if root_module not in initrd_contents:
                    raise RuntimeError(
                        f"{context.plan.storage.filesystem.value} target "
                        "initrd is missing its root filesystem driver"
                    )

        grub_cfg = target / "boot/grub/grub.cfg"
        if not grub_cfg.is_file():
            raise RuntimeError("GRUB configuration was not generated")
        config = grub_cfg.read_text(encoding="utf-8", errors="replace")
        if "menuentry " not in config or "vmlinuz-" not in config:
            raise RuntimeError("GRUB configuration has no Linux boot entry")

        guided = context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
        manual = context.plan.storage.mode is InstallMode.MANUAL
        vendor_only = guided or manual
        if vendor_only:
            loader = (
                target
                / "boot/efi"
                / commands.loader_path.replace("\\", "/").lstrip("/")
            )
            if not loader.is_file():
                raise RuntimeError(
                    f"AnduinOS vendor UEFI loader is missing: {loader}"
                )
            efi_loader = loader
            _verify_vendor_nvram(self.runner, context, commands)
            inspection_key = (
                "guided_esp_inspection" if guided else "manual_esp_inspection"
            )
            inspection = context.values.get(inspection_key)
            if isinstance(inspection, EspReuseInspection):
                verify_preserved_esp_tree(
                    inspection.preserved_entries,
                    target / "boot/efi",
                )
        else:
            fallback = target / "boot/efi" / commands.efi_fallback
            if not fallback.is_file():
                raise RuntimeError(
                    f"UEFI fallback loader is missing: {fallback}"
                )
            efi_loader = fallback
        expected_machine = (
            0x8664
            if context.plan.platform.architecture is Architecture.AMD64
            else 0xAA64
        )
        actual_machine = read_pe_machine(efi_loader)
        if actual_machine != expected_machine:
            raise RuntimeError(
                f"UEFI loader machine 0x{actual_machine:04x} does not match "
                f"expected 0x{expected_machine:04x}"
            )

        target_architecture = self.runner.run(
            ("chroot", str(target), "dpkg", "--print-architecture"),
            timeout=10,
        ).stdout.strip()
        if target_architecture != context.plan.platform.architecture.value:
            raise RuntimeError(
                f"Target userspace architecture is {target_architecture!r}"
            )

        if not vendor_only and commands.bios_required:
            bios_modules = target / "boot/grub/i386-pc"
            if not bios_modules.is_dir() or not (
                bios_modules / "normal.mod"
            ).is_file():
                raise RuntimeError("Legacy BIOS GRUB modules are missing")

    def cleanup(self, context: InstallContext) -> None:
        return None


def _verify_vendor_nvram(
    runner: CommandRunner,
    context: InstallContext,
    commands,
) -> None:
    devices = context.values.get("partition_devices", {})
    esp = str(devices.get("efi-system") or "")
    if not esp:
        raise RuntimeError("EFI System Partition is unresolved")
    partuuid = runner.run(
        ("blkid", "-s", "PARTUUID", "-o", "value", esp),
        timeout=10,
    ).stdout.strip()
    if not partuuid:
        raise RuntimeError("EFI System Partition has no PARTUUID")
    output = runner.run(commands.nvram_verify, timeout=30).stdout
    verify_nvram_entry(
        output,
        label="AnduinOS",
        partuuid=partuuid,
        loader=commands.loader_path,
    )


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target


def _verify_grub_install_options(
    runner: CommandRunner,
    target: Path,
    installs: tuple[tuple[str, ...], ...],
) -> None:
    result = runner.run(
        ("chroot", str(target), "grub-install", "--help"),
        timeout=30,
        log_output=False,
    )
    help_text = f"{result.stdout}\n{result.stderr}"
    planned_options = {
        argument.split("=", 1)[0]
        for command in installs
        for argument in command
        if argument.startswith("--")
    }
    unsupported = sorted(
        option for option in planned_options if option not in help_text
    )
    if unsupported:
        raise RuntimeError(
            "Target grub-install does not support planned option(s): "
            + ", ".join(unsupported)
        )


def _verify_grub_platform_modules(
    target: Path,
    installs: tuple[tuple[str, ...], ...],
) -> None:
    planned_targets = {
        argument.split("=", 1)[1]
        for command in installs
        for argument in command
        if argument.startswith("--target=")
    }
    unknown = sorted(planned_targets - GRUB_PLATFORM_MODULES.keys())
    if unknown:
        raise RuntimeError(
            "Unsupported GRUB platform target(s): " + ", ".join(unknown)
        )
    missing = tuple(
        target / GRUB_PLATFORM_MODULES[platform]
        for platform in sorted(planned_targets)
        if not (target / GRUB_PLATFORM_MODULES[platform]).is_file()
    )
    if missing:
        raise RuntimeError(
            "Target GRUB platform modules are missing: "
            + ", ".join(str(path) for path in missing)
        )


def _verify_grub_filesystem_modules(
    target: Path,
    installs: tuple[tuple[str, ...], ...],
    filesystem: Filesystem,
) -> None:
    module = GRUB_ADVANCED_FILESYSTEM_MODULES.get(filesystem)
    if module is None:
        return
    planned_targets = {
        argument.split("=", 1)[1]
        for command in installs
        for argument in command
        if argument.startswith("--target=")
    }
    missing = tuple(
        target / "usr/lib/grub" / platform / module
        for platform in sorted(planned_targets)
        if not (target / "usr/lib/grub" / platform / module).is_file()
    )
    if missing:
        raise RuntimeError(
            f"Target GRUB {filesystem.value} modules are missing: "
            + ", ".join(str(path) for path in missing)
        )


def read_pe_machine(path: Path) -> int:
    """Return the PE machine type of an EFI executable."""

    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise RuntimeError(f"UEFI loader is not a PE executable: {path}")
        pe_offset = int.from_bytes(header[0x3C:0x40], "little")
        stream.seek(pe_offset)
        pe_header = stream.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        raise RuntimeError(f"UEFI loader has an invalid PE header: {path}")
    return int.from_bytes(pe_header[4:6], "little")
