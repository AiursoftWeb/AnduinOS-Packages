"""Install and verify the unsigned GRUB foundation for Milestone 3C."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .boot_commands import build_boot_commands
from .command import CommandRunner
from .model import Architecture
from .steps import FailurePolicy, InstallContext
from .validation import validate_plan


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
        validate_plan(context.plan)

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
        commands = build_boot_commands(context.plan, str(target))
        context.values["boot_command_plan"] = commands
        self.runner.run(commands.initramfs, timeout=1200)
        for command in commands.installs:
            self.runner.run(command, timeout=300)
        self.runner.run(commands.configure, timeout=300)

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

        fallback = target / "boot/efi" / commands.efi_fallback
        if not fallback.is_file():
            raise RuntimeError(f"UEFI fallback loader is missing: {fallback}")
        expected_machine = (
            0x8664
            if context.plan.platform.architecture is Architecture.AMD64
            else 0xAA64
        )
        actual_machine = _read_pe_machine(fallback)
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

        if commands.bios_required:
            bios_modules = target / "boot/grub/i386-pc"
            if not bios_modules.is_dir() or not (
                bios_modules / "normal.mod"
            ).is_file():
                raise RuntimeError("Legacy BIOS GRUB modules are missing")

    def cleanup(self, context: InstallContext) -> None:
        return None


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target


def _read_pe_machine(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise RuntimeError(f"UEFI loader is not a PE executable: {path}")
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if (
        pe_offset + 6 > len(data)
        or data[pe_offset : pe_offset + 4] != b"PE\0\0"
    ):
        raise RuntimeError(f"UEFI loader has an invalid PE header: {path}")
    return int.from_bytes(data[pe_offset + 4 : pe_offset + 6], "little")
