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
from .model import Architecture, InstallMode
from .steps import FailurePolicy, InstallContext
from .storage_planning import GuidedCoexistenceExecutionPlan


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
            target / "usr/sbin/update-initramfs",
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
        guided_execution = context.values.get(
            "guided_storage_execution_plan"
        )
        guided = context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
        if guided:
            if not isinstance(
                guided_execution, GuidedCoexistenceExecutionPlan
            ):
                raise RuntimeError("Guided boot command plan is missing")
            commands = guided_execution.boot_commands
            installs = (commands.install,)
        else:
            commands = build_boot_commands(context.plan, str(target))
            installs = commands.installs
        context.values["boot_command_plan"] = commands
        _verify_grub_install_options(self.runner, target, installs)
        devices = context.values.get("partition_devices", {})
        context.log(
            "Bootloader target disk: "
            f"{context.plan.storage.disk.path} (selected disk only)"
        )
        if not guided and commands.bios_required:
            context.log(
                "Installing Legacy BIOS GRUB to "
                f"{context.plan.storage.disk.path}"
            )
        context.log(
            "Installing UEFI bootloader to "
            f"{devices.get('efi-system', 'the selected disk ESP')} "
            "mounted at /boot/efi"
        )
        if guided:
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
        self.runner.run(commands.initramfs, timeout=1200)
        for command in installs:
            if guided:
                emit_boundary(context, "guided-boot-files", "before")
            self.runner.run(command, timeout=300)
            if guided:
                emit_boundary(context, "guided-boot-files", "after")
        self.runner.run(commands.configure, timeout=300)
        if guided:
            emit_boundary(context, "guided-nvram", "before")
            self.runner.run(commands.nvram_create, timeout=30)
            _verify_guided_nvram(self.runner, context, commands)
            emit_boundary(context, "guided-nvram", "after")

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
        initramfs = {
            path.name.removeprefix("initrd.img-")
            for path in (target / "boot").glob("initrd.img-*")
            if path.is_file()
        }
        if not kernels or not kernels.intersection(initramfs):
            raise RuntimeError("No kernel has a matching initramfs")

        grub_cfg = target / "boot/grub/grub.cfg"
        if not grub_cfg.is_file():
            raise RuntimeError("GRUB configuration was not generated")
        config = grub_cfg.read_text(encoding="utf-8", errors="replace")
        if "menuentry " not in config or "vmlinuz-" not in config:
            raise RuntimeError("GRUB configuration has no Linux boot entry")

        guided = context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
        if guided:
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
            _verify_guided_nvram(self.runner, context, commands)
            inspection = context.values.get("guided_esp_inspection")
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

        if not guided and commands.bios_required:
            bios_modules = target / "boot/grub/i386-pc"
            if not bios_modules.is_dir() or not (
                bios_modules / "normal.mod"
            ).is_file():
                raise RuntimeError("Legacy BIOS GRUB modules are missing")

    def cleanup(self, context: InstallContext) -> None:
        return None


def _verify_guided_nvram(
    runner: CommandRunner,
    context: InstallContext,
    commands,
) -> None:
    devices = context.values.get("partition_devices", {})
    esp = str(devices.get("efi-system") or "")
    if not esp:
        raise RuntimeError("Guided EFI System Partition is unresolved")
    partuuid = runner.run(
        ("blkid", "-s", "PARTUUID", "-o", "value", esp),
        timeout=10,
    ).stdout.strip()
    if not partuuid:
        raise RuntimeError("Guided EFI System Partition has no PARTUUID")
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
