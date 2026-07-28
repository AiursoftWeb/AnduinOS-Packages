"""Pure command planning for the release-one boot matrix."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Architecture, InstallPlan, SecureBoot
from .validation import validate_plan


@dataclass(frozen=True)
class BootCommandPlan:
    initramfs: tuple[str, ...]
    installs: tuple[tuple[str, ...], ...]
    configure: tuple[str, ...]
    efi_fallback: str
    bios_required: bool


def build_boot_commands(plan: InstallPlan, target: str) -> BootCommandPlan:
    validate_plan(plan)
    chroot = ("chroot", target)
    installs: list[tuple[str, ...]] = []
    disk = plan.storage.disk.path

    if plan.platform.architecture is Architecture.AMD64:
        # The amd64 disk is deliberately portable between old BIOS and UEFI.
        installs.append(
            chroot
            + (
                "grub-install",
                "--target=i386-pc",
                "--recheck",
                disk,
            )
        )
        efi_target = "x86_64-efi"
        fallback = "EFI/BOOT/BOOTX64.EFI"
    else:
        efi_target = "arm64-efi"
        fallback = "EFI/BOOT/BOOTAA64.EFI"

    efi_install = [
        *chroot,
            "grub-install",
            f"--target={efi_target}",
            "--efi-directory=/boot/efi",
            "--bootloader-id=AnduinOS",
            "--recheck",
            "--no-nvram",
            "--force-extra-removable",
    ]
    if plan.platform.secure_boot is SecureBoot.ENABLED:
        efi_install.append("--uefi-secure-boot")
    installs.append(tuple(efi_install))
    return BootCommandPlan(
        initramfs=chroot + ("update-initramfs", "-u", "-k", "all"),
        installs=tuple(installs),
        configure=chroot + ("update-grub",),
        efi_fallback=fallback,
        bios_required=plan.platform.architecture is Architecture.AMD64,
    )
