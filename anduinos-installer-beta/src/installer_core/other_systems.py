"""Read-only discovery of UEFI Windows loaders on non-target disks."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .bootloader import read_pe_machine
from .command import CommandRunner
from .model import Architecture, Firmware
from .steps import FailurePolicy, InstallContext, StepSkipped
from .storage_inventory import StorageInventory, probe_storage_inventory


WINDOWS_LOADER_RELATIVE = Path("EFI/Microsoft/Boot/bootmgfw.efi")
WINDOWS_LOADER_GRUB_PATH = "/EFI/Microsoft/Boot/bootmgfw.efi"
WINDOWS_GRUB_SCRIPT = Path("etc/grub.d/42_anduinos_windows")
FAT_UUID_RE = re.compile(r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$")


@dataclass(frozen=True)
class WindowsBootloader:
    disk_stable_id: str
    partition_path: str
    partuuid: str
    filesystem_uuid: str


def discover_windows_bootloaders(
    inventory: StorageInventory,
    *,
    target_disk_id: str,
    architecture: Architecture,
    runner: CommandRunner,
    log: Callable[[str], None],
    scratch_root: Path = Path("/run/anduinos-installer"),
) -> tuple[WindowsBootloader, ...]:
    """Find canonical Windows EFI loaders without writing foreign disks."""

    expected_machine = (
        0x8664 if architecture is Architecture.AMD64 else 0xAA64
    )
    discovered: list[WindowsBootloader] = []
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    for disk in inventory.disks:
        if disk.identity.stable_id == target_disk_id:
            continue
        if not any(
            partition.is_windows_partition for partition in disk.partitions
        ):
            continue
        for partition in disk.partitions:
            filesystem_uuid = partition.filesystem_uuid.strip()
            if (
                not partition.is_efi_filesystem_candidate
                or any(partition.mountpoints)
                or not partition.identity.partuuid
                or not FAT_UUID_RE.fullmatch(filesystem_uuid)
            ):
                continue

            with tempfile.TemporaryDirectory(
                prefix="windows-esp-check-",
                dir=scratch_root,
            ) as directory:
                mounted = False
                try:
                    runner.run(
                        (
                            "mount",
                            "--read-only",
                            "--types",
                            "vfat",
                            "--options",
                            "nosuid,nodev,noexec",
                            partition.identity.path,
                            directory,
                        ),
                        timeout=30,
                    )
                    mounted = True
                    loader = Path(directory) / WINDOWS_LOADER_RELATIVE
                    if not loader.is_file():
                        continue
                    try:
                        machine = read_pe_machine(loader)
                    except (OSError, RuntimeError) as error:
                        log(
                            "Ignoring invalid Windows EFI loader on "
                            f"{partition.identity.path}: {error}"
                        )
                        continue
                    if machine != expected_machine:
                        log(
                            "Ignoring Windows EFI loader for another "
                            f"architecture on {partition.identity.path}"
                        )
                        continue
                    discovered.append(
                        WindowsBootloader(
                            disk_stable_id=disk.identity.stable_id,
                            partition_path=partition.identity.path,
                            partuuid=partition.identity.partuuid,
                            filesystem_uuid=filesystem_uuid.upper(),
                        )
                    )
                except Exception as error:
                    log(
                        "Could not inspect EFI System Partition "
                        f"{partition.identity.path} read-only: {error}"
                    )
                finally:
                    if mounted:
                        runner.run(("umount", directory), timeout=30)

    uuid_counts: dict[str, int] = {}
    for item in discovered:
        uuid_counts[item.filesystem_uuid] = (
            uuid_counts.get(item.filesystem_uuid, 0) + 1
        )
    unique = tuple(
        item
        for item in discovered
        if uuid_counts[item.filesystem_uuid] == 1
    )
    if len(unique) != len(discovered):
        log(
            "Ignoring Windows EFI loaders with duplicate filesystem UUIDs"
        )
    return tuple(
        sorted(
            unique,
            key=lambda item: (
                item.disk_stable_id,
                item.partuuid.lower(),
            ),
        )
    )


def build_windows_grub_script(
    bootloaders: tuple[WindowsBootloader, ...],
) -> str:
    """Generate a deterministic grub-mkconfig fragment for Windows EFI."""

    if not bootloaders:
        raise ValueError("No Windows bootloaders were supplied")
    lines = [
        "#!/bin/sh",
        "# Generated by AnduinOS Installer; foreign disks are never written.",
        "cat <<'ANDUINOS_WINDOWS_EOF'",
        "if [ \"${timeout_style}\" = hidden ]; then",
        "    set timeout_style=menu",
        "fi",
        "if [ \"${timeout}\" = 0 ]; then",
        "    set timeout=10",
        "fi",
    ]
    multiple = len(bootloaders) > 1
    for index, bootloader in enumerate(bootloaders, start=1):
        if not FAT_UUID_RE.fullmatch(bootloader.filesystem_uuid):
            raise ValueError("Invalid Windows EFI filesystem UUID")
        title = (
            f"Windows Boot Manager (disk {index})"
            if multiple
            else "Windows Boot Manager"
        )
        lines.extend(
            (
                f"# AnduinOS external Windows entry: "
                f"{bootloader.filesystem_uuid}",
                f"menuentry '{title}' --class windows --class os {{",
                "    insmod part_gpt",
                "    insmod fat",
                "    insmod chain",
                "    search --no-floppy --fs-uuid --set=root "
                f"{bootloader.filesystem_uuid}",
                f"    chainloader {WINDOWS_LOADER_GRUB_PATH}",
                "}",
            )
        )
    lines.extend(("ANDUINOS_WINDOWS_EOF", ""))
    return "\n".join(lines)


@dataclass
class CheckOtherDiskSystemsStep:
    runner: CommandRunner
    inventory_probe: object = probe_storage_inventory
    windows_probe: object = discover_windows_bootloaders
    id: str = "check-other-disk-systems"
    title: str = "Check systems on other disks"
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        if context.plan.platform.firmware is not Firmware.UEFI:
            raise RuntimeError("Other-disk system detection requires UEFI")
        self.runner.require_commands(("mount", "umount", "chroot"))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        inventory = self.inventory_probe()
        bootloaders = self.windows_probe(
            inventory,
            target_disk_id=context.plan.storage.disk.stable_id,
            architecture=context.plan.platform.architecture,
            runner=self.runner,
            log=context.log,
        )
        context.values["other_disk_windows_bootloaders"] = bootloaders
        script_path = target / WINDOWS_GRUB_SCRIPT
        if not bootloaders:
            if script_path.exists() or script_path.is_symlink():
                script_path.unlink()
                self.runner.run(
                    ("chroot", str(target), "update-grub"), timeout=300
                )
            raise StepSkipped("No UEFI Windows system found on other disks")

        script = build_windows_grub_script(bootloaders)
        _write_atomic(script_path, script, mode=0o755)
        context.values["other_disk_windows_script"] = script
        for bootloader in bootloaders:
            context.log(
                "Adding Windows Boot Manager from read-only EFI System "
                f"Partition {bootloader.partition_path}"
            )
        self.runner.run(
            ("chroot", str(target), "update-grub"), timeout=300
        )

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        bootloaders = context.values.get("other_disk_windows_bootloaders")
        expected_script = context.values.get("other_disk_windows_script")
        if not isinstance(bootloaders, tuple) or not bootloaders:
            raise RuntimeError("Windows bootloader discovery did not execute")
        script_path = target / WINDOWS_GRUB_SCRIPT
        if (
            not script_path.is_file()
            or script_path.stat().st_mode & 0o777 != 0o755
            or script_path.read_text(encoding="utf-8") != expected_script
        ):
            raise RuntimeError("Windows GRUB source configuration is invalid")
        grub_cfg = target / "boot/grub/grub.cfg"
        if not grub_cfg.is_file():
            raise RuntimeError("GRUB configuration is missing")
        config = grub_cfg.read_text(encoding="utf-8", errors="replace")
        missing = tuple(
            item.filesystem_uuid
            for item in bootloaders
            if (
                f"# AnduinOS external Windows entry: "
                f"{item.filesystem_uuid}"
            )
            not in config
        )
        if missing:
            raise RuntimeError(
                "Windows entries are missing from GRUB configuration: "
                + ", ".join(missing)
            )

    def cleanup(self, context: InstallContext) -> None:
        return None


def _write_atomic(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    if not context.values.get("chroot_environment_ready"):
        raise RuntimeError("Target chroot environment is not active")
    return target
