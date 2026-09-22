"""Install and verify the unsigned GRUB foundation for Milestone 3C."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import tempfile

from .boot_commands import build_boot_commands
from .command import CommandRunner
from .esp import (
    EspReuseInspection,
    nvram_boot_order,
    verify_nvram_entry,
    verify_preserved_esp_tree,
)
from .execution_boundaries import emit_boundary
from .model import Architecture, Filesystem, Firmware, InstallMode, InstallPlan
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
        if (
            context.plan.platform.firmware is Firmware.UEFI
            and context.plan.boot.external_target
            and context.plan.boot.install_fallback_path
        ):
            self.runner.require_commands(("openssl", "sbattach", "sbverify"))

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
        explicit_nvram = bool(commands.nvram_create)
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
        if explicit_nvram:
            context.log(
                "Creating and placing an AnduinOS UEFI Boot#### entry first"
            )
        else:
            context.log("UEFI Boot#### entries will not be modified")
        if not vendor_only:
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
        if explicit_nvram and getattr(commands, "efi_fallback", ""):
            _deploy_portable_fallback(
                self.runner,
                target,
                context.plan,
            )
        if explicit_nvram:
            if vendor_only:
                emit_boundary(context, f"{prefix}-nvram", "before")
            self.runner.run(commands.nvram_create, timeout=30)
            _ensure_vendor_nvram_first(self.runner, context, commands)
            if vendor_only:
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
        explicit_nvram = bool(commands.nvram_create)
        if explicit_nvram:
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
            _verify_vendor_nvram(
                self.runner,
                context,
                commands,
                require_first=True,
            )
            if getattr(commands, "efi_fallback", ""):
                _verify_portable_fallback(
                    self.runner,
                    target,
                    context.plan,
                )
        else:
            fallback = target / "boot/efi" / commands.efi_fallback
            if not fallback.is_file():
                raise RuntimeError(
                    f"UEFI fallback loader is missing: {fallback}"
                )
            efi_loader = fallback
        if vendor_only:
            inspection_key = (
                "guided_esp_inspection" if guided else "manual_esp_inspection"
            )
            inspection = context.values.get(inspection_key)
            if isinstance(inspection, EspReuseInspection):
                verify_preserved_esp_tree(
                    inspection.preserved_entries,
                    target / "boot/efi",
                )
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
    *,
    require_first: bool = False,
) -> tuple[str, str]:
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
    number = verify_nvram_entry(
        output,
        label="AnduinOS",
        partuuid=partuuid,
        loader=commands.loader_path,
        require_first=require_first,
    )
    return number, output


def _ensure_vendor_nvram_first(
    runner: CommandRunner,
    context: InstallContext,
    commands,
) -> None:
    number, output = _verify_vendor_nvram(runner, context, commands)
    current_order = nvram_boot_order(output)
    desired_order = (number,) + tuple(
        item for item in current_order if item != number
    )
    if current_order != desired_order:
        runner.run(
            ("efibootmgr", "--bootorder", ",".join(desired_order)),
            timeout=30,
        )
    _verify_vendor_nvram(
        runner,
        context,
        commands,
        require_first=True,
    )


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target


def _portable_fallback_paths(
    target: Path,
    plan: InstallPlan,
) -> tuple[tuple[Path, Path], ...]:
    suffix = (
        "x64" if plan.platform.architecture is Architecture.AMD64 else "aa64"
    )
    boot_name = "BOOTX64.EFI" if suffix == "x64" else "BOOTAA64.EFI"
    vendor = target / "boot/efi/EFI/AnduinOS"
    fallback = target / "boot/efi/EFI/BOOT"
    return (
        (vendor / f"grub{suffix}.efi", fallback / f"grub{suffix}.efi"),
        (vendor / f"mm{suffix}.efi", fallback / f"mm{suffix}.efi"),
        (vendor / "grub.cfg", fallback / "grub.cfg"),
        (vendor / f"shim{suffix}.efi", fallback / boot_name),
    )


def _verify_signed_image(
    runner: CommandRunner,
    path: Path,
    expected_machine: int,
) -> None:
    if read_pe_machine(path) != expected_machine:
        raise RuntimeError(
            f"Portable EFI loader has the wrong architecture: {path}"
        )
    # Listing an Authenticode record is not signature verification. Extract
    # the embedded PKCS#7 certificates and require one of them to validate the
    # PE digest, while firmware testing separately proves db/dbx acceptance.
    with tempfile.TemporaryDirectory(
        prefix="anduinos-portable-efi-"
    ) as directory:
        signature = Path(directory) / "signature.der"
        runner.run(
            ("sbattach", "--detach", str(signature), str(path)),
            timeout=30,
        )
        certificates = runner.run(
            (
                "openssl", "pkcs7", "-inform", "DER", "-in",
                str(signature), "-print_certs",
            ),
            timeout=30,
        ).stdout
        for index, pem in enumerate(re.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            certificates,
            re.DOTALL,
        )):
            certificate = Path(directory) / f"certificate-{index}.pem"
            certificate.write_text(pem + "\n", encoding="ascii")
            result = runner.run(
                ("sbverify", "--cert", str(certificate), str(path)),
                check=False,
                timeout=30,
            )
            if result.returncode == 0:
                return
    raise RuntimeError(f"Portable EFI loader signature is invalid: {path}")


def _deploy_portable_fallback(
    runner: CommandRunner,
    target: Path,
    plan: InstallPlan,
) -> None:
    """Deploy the installer-owned portable chain, never GRUB's fallback set.

    All UEFI ``grub-install`` calls intentionally use
    ``--no-extra-removable``, even for an external erase-disk plan.  Allowing
    grub-install to manage EFI/BOOT can install shim's ``fb*.efi`` NVRAM
    registrar and reproduce the ResetSystem loop tracked by issue #422.
    Portable mode is instead expressed by ``install_fallback_path`` and lands
    here after the vendor chain exists.  The copied chain boots shim directly,
    retains MokManager, excludes the registrar, and is verified below.

    Keep this separation explicit: changing the grub-install flag is not an
    equivalent implementation of portable mode.
    """

    if (
        plan.storage.mode is not InstallMode.ERASE_DISK
        or not plan.boot.external_target
        or not plan.boot.install_fallback_path
    ):
        raise RuntimeError("Portable fallback is not authorized for this plan")
    fallback = target / "boot/efi/EFI/BOOT"
    if fallback.is_symlink():
        raise RuntimeError("Portable EFI fallback directory is redirected")
    fallback.mkdir(parents=True, exist_ok=True)
    if fallback.resolve() != fallback:
        raise RuntimeError("Portable EFI fallback directory is redirected")
    expected_machine = (
        0x8664
        if plan.platform.architecture is Architecture.AMD64
        else 0xAA64
    )
    pairs = _portable_fallback_paths(target, plan)
    for source, destination in pairs:
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(
                f"Portable EFI source is missing or redirected: {source}"
            )
        temporary = destination.with_name(f".{destination.name}.anduinos-new")
        if temporary.exists() or temporary.is_symlink():
            raise RuntimeError(
                f"Portable EFI staging path already exists: {temporary}"
            )
        try:
            shutil.copyfile(source, temporary)
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            if destination.suffix.lower() == ".efi":
                _verify_signed_image(runner, temporary, expected_machine)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    directory = os.open(fallback, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    _verify_portable_fallback(runner, target, plan)


def _verify_portable_fallback(
    runner: CommandRunner,
    target: Path,
    plan: InstallPlan,
) -> None:
    expected_machine = (
        0x8664
        if plan.platform.architecture is Architecture.AMD64
        else 0xAA64
    )
    for source, destination in _portable_fallback_paths(target, plan):
        if destination.is_symlink() or not destination.is_file():
            raise RuntimeError(
                f"Portable EFI fallback is missing or redirected: {destination}"
            )
        if source.read_bytes() != destination.read_bytes():
            raise RuntimeError(
                f"Portable EFI fallback does not match vendor payload: {destination}"
            )
        if destination.suffix.lower() == ".efi":
            _verify_signed_image(runner, destination, expected_machine)
    suffix = "x64" if plan.platform.architecture is Architecture.AMD64 else "aa64"
    registrar = target / "boot/efi/EFI/BOOT" / f"fb{suffix}.efi"
    if registrar.exists() or registrar.is_symlink():
        raise RuntimeError(
            "Portable EFI fallback must not contain the NVRAM registration loader"
        )


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
