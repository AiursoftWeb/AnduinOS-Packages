"""Read-only boot diagnosis and bounded UEFI repair for an offline installation."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import Partition
from .offline_env import opened_system, system_root
from .storage import _descendants, _identity, _safe_regular_file, mounted_readonly, resolve_target


@dataclass(frozen=True)
class Esp:
    path: str
    identity: str
    partuuid: str
    number: int
    disk: str
    mounted: bool


def _efi_spec(root: Path) -> tuple[str, str]:
    fstab = _safe_regular_file(root, "etc/fstab")
    if fstab is None or fstab.stat().st_size > 128 * 1024:
        raise RuntimeError("The installed system has no readable /etc/fstab")
    entries = [line.split() for line in fstab.read_text(encoding="utf-8", errors="replace").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if any(len(entry) >= 2 and entry[1] == "/boot" for entry in entries):
        raise RuntimeError("A separate /boot partition needs manual repair")
    matches = [entry for entry in entries if len(entry) >= 3 and entry[1:3] == ["/boot/efi", "vfat"]]
    if len(matches) != 1:
        raise RuntimeError("Exactly one FAT EFI partition must be declared in /etc/fstab")
    match = re.fullmatch(r"(UUID|PARTUUID)=([A-Fa-f0-9-]{4,64})", matches[0][0])
    if not match:
        raise RuntimeError("The EFI partition must use an explicit UUID or PARTUUID")
    return match.group(1).lower(), match.group(2).lower()


def _locate_esp(partition: Partition, spec: tuple[str, str], *, run=subprocess.run) -> Esp:
    result = run(
        ["lsblk", "--json", "--bytes", "--paths", "--tree", "--output",
         "PATH,SIZE,TYPE,MAJ:MIN,UUID,PARTUUID,PARTTYPE,FSTYPE,PARTN,MOUNTPOINTS"],
        capture_output=True, text=True, timeout=15, check=False,
        env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not identify the EFI partition")
    try:
        roots = json.loads(result.stdout)["blockdevices"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("lsblk returned invalid EFI partition data") from error
    matches: list[Esp] = []
    for disk in roots if isinstance(roots, list) else ():
        if not isinstance(disk, dict) or disk.get("path") != partition.parent:
            continue
        for node in _descendants(disk):
            if node.get("type") != "part" or str(node.get(spec[0]) or "").lower() != spec[1]:
                continue
            if str(node.get("fstype") or "").lower() not in {"vfat", "fat", "fat32"}:
                raise RuntimeError("The declared EFI partition is not FAT")
            if str(node.get("parttype") or "").lower() not in {
                "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "0xef", "ef",
            }:
                raise RuntimeError("The declared FAT partition is not marked as an EFI System Partition")
            path = str(node.get("path") or "")
            if (not path.startswith("/dev/") or not str(node.get("partn") or "").isdigit()
                    or int(node["partn"]) <= 0 or not node.get("partuuid")):
                raise RuntimeError("The EFI device or partition number is invalid")
            size = int(node.get("size") or 0)
            matches.append(Esp(
                path=path,
                identity=_identity(path, str(node.get("maj:min") or ""),
                                   str(node.get("uuid") or ""), str(node.get("partuuid") or ""), size),
                partuuid=str(node.get("partuuid") or ""),
                number=int(node["partn"]), disk=partition.parent,
                mounted=any(node.get("mountpoints") or ()),
            ))
    if len(matches) != 1:
        raise RuntimeError("The EFI partition was not uniquely found on the selected disk")
    return matches[0]


def _boot_files(root: Path) -> tuple[list[str], list[str], bool]:
    boot = root / "boot"
    if not boot.is_dir() or not boot.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        return [], [], False
    kernels = sorted(path.name.removeprefix("vmlinuz-") for path in boot.glob("vmlinuz-*")
                     if path.is_file() and not path.is_symlink() and path.stat().st_size > 0)
    complete = [version for version in kernels
                if (boot / f"initrd.img-{version}").is_file()
                and (boot / f"initrd.img-{version}").stat().st_size > 0]
    config = _safe_regular_file(root, "boot/grub/grub.cfg")
    configured = False
    if config is not None and 0 < config.stat().st_size <= 4 * 1024 * 1024:
        lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
        configured = any(
            any(re.search(rf"^\s*linux(?:efi)?\s+.*\bvmlinuz-{re.escape(version)}(?:\s|$)", line)
                for line in lines)
            and any(re.search(rf"^\s*initrd(?:efi)?\s+.*\binitrd\.img-{re.escape(version)}(?:\s|$)", line)
                    for line in lines)
            for version in complete
        )
    return kernels, complete, configured


def _nvram_entry(esp: Esp, *, run=subprocess.run) -> bool | None:
    result = run(["efibootmgr", "--verbose"], capture_output=True, text=True,
                 timeout=15, check=False)
    if result.returncode:
        return None
    order = re.search(r"(?m)^BootOrder:\s*([0-9A-Fa-f]{4}(?:,[0-9A-Fa-f]{4})*)\s*$", result.stdout)
    if not order:
        return None
    boot_order = set(order.group(1).upper().split(","))
    loader = rf"\EFI\AnduinOS\shim{'x64' if platform.machine() == 'x86_64' else 'aa64'}.efi"
    matching_entry = False
    for line in result.stdout.splitlines():
        match = re.match(
            r"^Boot([0-9A-Fa-f]{4})(\*)?\s+(.*?)\s+HD\(\d+,GPT,([^,]+),[^)]*\)/"
            r"(?:File\()?(\\[^)\s]+)\)?", line.strip(),
        )
        if (match and match.group(3) == "AnduinOS"
                and match.group(4).strip("{}").lower() == esp.partuuid.strip("{}").lower()
                and match.group(5).replace("/", "\\").lower() == loader.lower()):
            matching_entry = True
            if match.group(2) and match.group(1).upper() in boot_order:
                return True
    return None if matching_entry else False


def _firmware_available() -> bool:
    return Path("/sys/firmware/efi/efivars").is_dir()


def diagnose_boot(path: str, identity: str, *, run=subprocess.run,
                  mount_base: Path = Path("/run/anduinos-rescue-center")) -> dict[str, object]:
    partition = resolve_target(path, identity, run=run)
    if partition.active_system or partition.mountpoints:
        raise RuntimeError("The selected installation must be offline and unmounted")
    issues: list[str] = []
    with mounted_readonly(partition.path, partition.filesystem, run=run, mount_base=mount_base) as top:
        root = system_root(top, partition)
        kernels, complete, config = _boot_files(root)
        if not kernels:
            issues.append("No installed kernel was found in /boot")
        if len(complete) != len(kernels):
            issues.append("At least one kernel has no matching initrd")
        if not config:
            issues.append("GRUB configuration is missing or does not reference a complete kernel/initrd pair")
        try:
            esp = _locate_esp(partition, _efi_spec(root), run=run)
        except RuntimeError as error:
            esp = None
            issues.append(str(error))
        vendor_loader = False
        if esp is not None:
            if esp.mounted:
                issues.append("The EFI partition is already mounted; unmount it before repair")
            else:
                with mounted_readonly(esp.path, "vfat", run=run, mount_base=mount_base) as efi:
                    vendor = efi / "EFI/AnduinOS"
                    suffix = "x64" if platform.machine() == "x86_64" else "aa64"
                    vendor_loader = all((vendor / name).is_file() and (vendor / name).stat().st_size > 0
                                        for name in (f"shim{suffix}.efi", f"grub{suffix}.efi", "grub.cfg"))
                if not vendor_loader:
                    issues.append("The AnduinOS EFI loader is missing")
        nvram = _nvram_entry(esp, run=run) if esp is not None else None
        if nvram is False:
            issues.append("No matching AnduinOS firmware boot entry was found")
        elif esp is not None and nvram is None:
            issues.append("An active AnduinOS firmware boot entry could not be verified in BootOrder")
    firmware_available = _firmware_available()
    if not firmware_available:
        issues.append("The Live session was not booted with accessible UEFI variables")
    return {
        "schema": 1, "target": partition.path,
        "esp": esp.path if esp else "", "esp_identity": esp.identity if esp else "",
        "kernel_count": len(kernels), "complete_pairs": len(complete),
        "grub_config": config, "efi_loader": vendor_loader,
        "nvram_entry": nvram, "issues": issues,
        "repairable": bool(esp and not esp.mounted and kernels and nvram is not None and firmware_available
                           and platform.machine() in {"x86_64", "aarch64"}),
        "changes": ["Rebuild installed initrds", "Install GRUB only in EFI/AnduinOS",
                    "Regenerate GRUB configuration",
                    "Create an AnduinOS firmware entry first in BootOrder only if missing"],
    }


def _run_checked(run, command: list[str], timeout: int) -> str:
    result = run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeError(detail or f"Boot repair command failed: {command[0]}")
    return result.stdout


def repair_boot(path: str, identity: str, esp_identity: str, *, run=subprocess.run,
                mount_base: Path = Path("/run/anduinos-rescue-center")) -> dict[str, object]:
    report = diagnose_boot(path, identity, run=run, mount_base=mount_base)
    if not report["repairable"] or report["esp_identity"] != esp_identity:
        raise RuntimeError("The selected EFI partition changed or cannot be repaired safely")
    partition = resolve_target(path, identity, run=run)
    with mounted_readonly(partition.path, partition.filesystem, run=run, mount_base=mount_base) as top:
        root = system_root(top, partition)
        esp = _locate_esp(partition, _efi_spec(root), run=run)
    if esp.identity != esp_identity or esp.mounted:
        raise RuntimeError("The selected EFI partition changed before repair")
    architecture = "x86_64-efi" if platform.machine() == "x86_64" else "arm64-efi"
    suffix = "x64" if architecture == "x86_64-efi" else "aa64"
    with opened_system(path, identity, esp=esp.path, run=run, mount_base=mount_base) as (root, _partition):
        for tool in ("usr/bin/dracut", "usr/bin/lsinitrd", "usr/sbin/grub-install", "usr/sbin/update-grub"):
            if not (root / tool).is_file():
                raise RuntimeError(f"Installed boot repair tool is missing: /{tool}")
        _run_checked(run, ["chroot", str(root), "dracut", "--force", "--no-hostonly",
                           "--no-hostonly-cmdline", "--omit",
                           "dmsquash-live dmsquash-live-autooverlay livenet anduinos-live-layers",
                           "--regenerate-all"], 1200)
        _, complete, _ = _boot_files(root)
        if not complete:
            raise RuntimeError("Dracut produced no matching kernel/initrd pair")
        forbidden = {"dmsquash-live", "dmsquash-live-autooverlay", "livenet", "anduinos-live-layers"}
        for version in complete:
            modules = set(_run_checked(
                run, ["chroot", str(root), "lsinitrd", "-m", f"/boot/initrd.img-{version}"], 60,
            ).splitlines())
            if forbidden.intersection(modules):
                raise RuntimeError("An installed-system initrd still contains Live boot modules")
            if partition.filesystem == "btrfs" and "anduinos-btrfs-snapshots-manager" not in modules:
                raise RuntimeError("The Btrfs initrd lacks the AnduinOS recovery module")
        _run_checked(run, ["chroot", str(root), "grub-install", f"--target={architecture}",
                           "--efi-directory=/boot/efi", "--bootloader-id=AnduinOS", "--recheck",
                           "--no-nvram", "--no-extra-removable", "--uefi-secure-boot"], 300)
        _run_checked(run, ["chroot", str(root), "update-grub"], 300)
        vendor = root / "boot/efi/EFI/AnduinOS"
        _, complete, config = _boot_files(root)
        if (not all((vendor / name).is_file() and (vendor / name).stat().st_size > 0
                    for name in (f"shim{suffix}.efi", f"grub{suffix}.efi", "grub.cfg"))
                or not complete or not config):
            raise RuntimeError("Boot repair did not produce a complete kernel, GRUB and EFI chain")
        if report["nvram_entry"] is False:
            if not _firmware_available():
                raise RuntimeError("EFI files were repaired, but firmware variables are unavailable")
            _run_checked(run, ["efibootmgr", "--create", "--disk", esp.disk, "--part",
                               str(esp.number), "--label", "AnduinOS", "--loader",
                               rf"\EFI\AnduinOS\shim{suffix}.efi"], 30)
    return diagnose_boot(path, identity, run=run, mount_base=mount_base)
