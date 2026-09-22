"""Pure command planning for the release-one boot matrix."""

from __future__ import annotations

from dataclasses import dataclass

from .layout import build_erase_disk_layout
from .model import Architecture, Firmware, InstallMode, InstallPlan
from .validation import validate_plan


@dataclass(frozen=True)
class BootCommandPlan:
    initrd: tuple[str, ...]
    installs: tuple[tuple[str, ...], ...]
    configure: tuple[str, ...]
    efi_fallback: str
    bios_required: bool
    nvram_create: tuple[str, ...] = ()
    nvram_verify: tuple[str, ...] = ()
    loader_path: str = ""


@dataclass(frozen=True)
class GuidedBootCommandPlan:
    """Vendor-only shared-ESP writes plus an explicit NVRAM update."""

    initrd: tuple[str, ...]
    install: tuple[str, ...]
    configure: tuple[str, ...]
    nvram_create: tuple[str, ...]
    nvram_verify: tuple[str, ...]
    loader_path: str


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
    ]
    # KEEP --no-extra-removable ON EVERY UEFI INSTALL, including an external
    # erase-disk install that deliberately receives a portable EFI/BOOT path.
    #
    # Ubuntu GRUB 2.14 installs its removable-media chain by default and only
    # exposes --no-extra-removable to opt out; the old
    # --force-extra-removable option no longer exists in Resolute.  We must
    # still opt out because grub-install's chain may contain shim's fallback
    # NVRAM registrar (fb*.efi).  On firmware affected by AnduinOS issue #422,
    # booting that registrar can create/select an entry, call ResetSystem, and
    # then be selected again indefinitely.
    #
    # install_fallback_path is therefore NOT permission to omit this option.
    # It authorizes InstallBootloaderStep to copy a narrowly defined direct
    # chain (shim -> GRUB, MokManager and grub.cfg, explicitly excluding
    # fb*.efi) from EFI/AnduinOS to EFI/BOOT after grub-install has completed
    # and the signed files have been verified.  Internal, coexistence and
    # manual installs never receive that post-install copy.  Do not turn the
    # presence of this option into the portable-mode switch.
    creates_nvram_entry = plan.platform.firmware is Firmware.UEFI
    if creates_nvram_entry:
        # An installed system must not rely on shim's removable-media
        # fallback application to create its first NVRAM entry and reboot.
        # Some firmware keeps selecting that fallback entry after ResetSystem,
        # producing an endless "Reset System" loop.
        efi_install.append("--no-extra-removable")
        if not plan.boot.install_fallback_path:
            fallback = ""
    if plan.platform.firmware is Firmware.UEFI:
        efi_install.append("--uefi-secure-boot")
    installs.append(tuple(efi_install))
    loader = guided_loader_path(plan) if creates_nvram_entry else ""
    esp_partition_number = build_erase_disk_layout(plan).partition(
        "efi-system"
    ).number
    return BootCommandPlan(
        initrd=chroot
        + (
            "dracut",
            "--force",
            "--no-hostonly",
            "--no-hostonly-cmdline",
            "--omit",
            "dmsquash-live dmsquash-live-autooverlay livenet anduinos-live-layers",
            "--regenerate-all",
        ),
        installs=tuple(installs),
        configure=chroot + ("update-grub",),
        efi_fallback=fallback,
        bios_required=plan.platform.architecture is Architecture.AMD64,
        nvram_create=(
            (
                "efibootmgr",
                "--create",
                "--disk",
                disk,
                "--part",
                str(esp_partition_number),
                "--label",
                "AnduinOS",
                "--loader",
                loader,
            )
            if creates_nvram_entry
            else ()
        ),
        nvram_verify=(
            ("efibootmgr", "--verbose") if creates_nvram_entry else ()
        ),
        loader_path=loader,
    )


def build_guided_coexistence_boot_commands(
    plan: InstallPlan,
    target: str,
    *,
    disk_path: str,
    esp_partition_number: int,
) -> GuidedBootCommandPlan:
    """Build UEFI commands that never write a shared fallback loader."""

    if plan.storage.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise ValueError("Plan is not guided coexistence")
    return _build_vendor_only_boot_commands(
        plan,
        target,
        disk_path=disk_path,
        esp_partition_number=esp_partition_number,
    )


def build_manual_boot_commands(
    plan: InstallPlan,
    target: str,
    *,
    disk_path: str,
    esp_partition_number: int,
) -> GuidedBootCommandPlan:
    """Build the same vendor-only UEFI policy for a manual plan."""

    if plan.storage.mode is not InstallMode.MANUAL:
        raise ValueError("Plan is not manual partitioning")
    return _build_vendor_only_boot_commands(
        plan,
        target,
        disk_path=disk_path,
        esp_partition_number=esp_partition_number,
    )


def _build_vendor_only_boot_commands(
    plan: InstallPlan,
    target: str,
    *,
    disk_path: str,
    esp_partition_number: int,
) -> GuidedBootCommandPlan:
    if plan.platform.firmware is not Firmware.UEFI:
        raise ValueError("Vendor-only boot installation requires UEFI firmware")
    if esp_partition_number <= 0:
        raise ValueError("EFI System Partition number must be positive")

    chroot = ("chroot", target)
    efi_target = (
        "x86_64-efi"
        if plan.platform.architecture is Architecture.AMD64
        else "arm64-efi"
    )
    install = [
        *chroot,
        "grub-install",
        f"--target={efi_target}",
        "--efi-directory=/boot/efi",
        "--bootloader-id=AnduinOS",
        "--recheck",
        "--no-nvram",
        # Shared/reused ESPs must never let grub-install populate EFI/BOOT.
        # See the issue-#422 invariant in build_boot_commands above.
        "--no-extra-removable",
    ]
    install.append("--uefi-secure-boot")
    loader = guided_loader_path(plan)
    return GuidedBootCommandPlan(
        initrd=chroot
        + (
            "dracut",
            "--force",
            "--no-hostonly",
            "--no-hostonly-cmdline",
            "--omit",
            "dmsquash-live dmsquash-live-autooverlay livenet anduinos-live-layers",
            "--regenerate-all",
        ),
        install=tuple(install),
        configure=chroot + ("update-grub",),
        nvram_create=(
            "efibootmgr",
            "--create",
            "--disk",
            disk_path,
            "--part",
            str(esp_partition_number),
            "--label",
            "AnduinOS",
            "--loader",
            loader,
        ),
        nvram_verify=("efibootmgr", "--verbose"),
        loader_path=loader,
    )


def guided_loader_path(plan: InstallPlan) -> str:
    architecture = (
        "x64" if plan.platform.architecture is Architecture.AMD64 else "aa64"
    )
    # Firmware enforcement can be enabled after installation. Keep the same
    # signed entry point in both states; MOK enrollment also requires shim.
    executable = f"shim{architecture}.efi"
    return rf"\EFI\AnduinOS\{executable}"
