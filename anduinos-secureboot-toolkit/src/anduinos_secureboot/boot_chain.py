"""Conservative local repair of the current AnduinOS UEFI vendor chain.

No package installation, fallback loader, firmware keys or foreign boot entry
is modified. Portable/removable-media boot policy belongs to the installer.
"""

from contextlib import ExitStack
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile

from .inspect import Runner


ESP = Path("/boot/efi")
ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"


def setup_mode(efi: Path) -> bool | None:
    try:
        data = (efi / "efivars" / f"SetupMode-{EFI_GLOBAL}").read_bytes()
        return bool(data[4]) if len(data) == 5 and data[4] in (0, 1) else None
    except OSError:
        return None


def command(runner: Runner, args: list[str]) -> str:
    result = runner.run(args, timeout=60)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Command failed: {args[0]}")
    return result.stdout


@dataclass(frozen=True)
class BootEntry:
    number: str
    partition: int
    uuid: str
    loader: str


def entries(output: str) -> tuple[BootEntry, ...]:
    # Only the exact vendor label/path and GPT hard-drive device paths qualify.
    pattern = (
        r"^Boot([0-9A-Fa-f]{4})\*?\s+AnduinOS\s+.*?"
        r"HD\((\d+),GPT,([0-9a-fA-F-]{36}),[^)]*\)/"
        r"(?:File\()? (\\EFI\\AnduinOS\\(?:shim|grub)(?:x64|aa64)\.efi)\)?[ \t]*$"
    )
    return tuple(BootEntry(n.upper(), int(p), u.lower(), f)
                 for n, p, u, f in re.findall(pattern, output, re.MULTILINE | re.I | re.X))


def variable(output: str, name: str) -> str:
    match = re.search(rf"^{name}:\s*([0-9A-Fa-f,]+)\s*$", output, re.MULTILINE)
    return match[1].upper() if match else ""


def current_loader(runner: Runner) -> str:
    """Read-only hint; privileged preparation performs full disk validation."""
    try:
        output = command(runner, ["efibootmgr", "--verbose"])
        if variable(output, "BootNext"):
            return "unknown"
        current = variable(output, "BootCurrent")
        boot_entries = entries(output)
        entry = next((e for e in boot_entries if e.number == current), None)
        if entry:
            # A successful repair keeps the old current entry for recovery.
            replacements = [e for e in boot_entries
                            if e.number in variable(output, "BootOrder").split(",")
                            and e.uuid == entry.uuid and e.partition == entry.partition]
            entry = replacements[0] if len(replacements) == 1 else None
        if entry:
            return "shim" if "\\shim" in entry.loader.lower() else "grub"
        if re.search(rf"^Boot{re.escape(current)}\*?\s+.*\\grub(?:x64|aa64)\.efi(?:\)|[ \t]*$)", output, re.M | re.I):
            return "grub"
    except (ValueError, OSError):
        pass
    return "unknown"


@dataclass(frozen=True)
class Target:
    disk: str
    entry: BootEntry
    order: str
    nvram: str
    device: str


def partition_number(device: str) -> int:
    # PARTN is not a lsblk column on every supported Ubuntu release.
    return int((Path("/sys/class/block") / Path(device).name / "partition").read_text())


def resolve_target(runner: Runner, esp: Path = ESP) -> Target:
    mount = json.loads(command(runner, [
        "findmnt", "--json", "--mountpoint", str(esp),
        "--output", "SOURCE,TARGET,FSTYPE,OPTIONS",
    ])).get("filesystems", [])
    if (len(mount) != 1 or mount[0].get("target") != str(esp)
            or mount[0].get("fstype") != "vfat"
            or "rw" not in mount[0].get("options", "").split(",")
            or esp.resolve() != esp):
        raise ValueError("The mounted writable ESP could not be identified safely")
    disks = json.loads(command(runner, [
        "lsblk", "--json", "--paths", "--output",
        "PATH,TYPE,PARTTYPE,PARTUUID,PKNAME",
    ])).get("blockdevices", [])
    partitions = {}

    def visit(nodes):
        for node in nodes:
            if (node.get("parttype") or "").lower() == ESP_TYPE:
                partitions[node["path"]] = node
            visit(node.get("children", []))

    visit(disks)
    if len(partitions) != 1:
        raise ValueError("Multiple or missing ESPs; automatic repair requires manual review")
    part = next(iter(partitions.values()))
    if (part.get("type") != "part"
            or Path(mount[0]["source"]).resolve() != Path(part["path"]).resolve()
            or not (part.get("pkname") or "").startswith("/dev/")):
        raise ValueError("The ESP does not match a physical disk partition")
    output = command(runner, ["efibootmgr", "--verbose"])
    known_entries = entries(output)
    current = next((e for e in known_entries if e.number == variable(output, "BootCurrent")), None)
    candidates = tuple(e for e in known_entries
                       if e.number in variable(output, "BootOrder").split(","))
    if (len(candidates) != 1 or current is None
            or current.uuid != candidates[0].uuid or current.partition != candidates[0].partition
            or candidates[0].uuid != (part.get("partuuid") or "").lower()
            or candidates[0].partition != partition_number(part["path"])):
        raise ValueError("Current AnduinOS boot entry and ESP are missing or ambiguous")
    entry = candidates[0]
    order = variable(output, "BootOrder")
    if order.split(",").count(entry.number) != 1 or variable(output, "BootNext"):
        raise ValueError("BootOrder or BootNext requires manual review")
    vendor = esp / "EFI/AnduinOS"
    if vendor.resolve() != vendor or not vendor.is_dir():
        raise ValueError("AnduinOS EFI directory is missing or redirected")
    cfg = vendor / "grub.cfg"
    if cfg.is_symlink() or not cfg.is_file() or not cfg.read_text().strip():
        raise ValueError("AnduinOS EFI GRUB configuration is missing")
    return Target(part["pkname"], entry, order, output, part["path"])


def verify_image(path: Path, machine: int, runner: Runner) -> None:
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError(f"Invalid EFI executable: {path}")
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        pe = stream.read(6)
    if len(pe) != 6 or pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != machine:
        raise ValueError(f"Wrong EFI architecture: {path}")
    # --list does not verify a signature. Verify the PE digest against an
    # embedded signing certificate; package integrity establishes provenance.
    with tempfile.TemporaryDirectory(prefix="anduinos-efi-verify-") as directory:
        signature = Path(directory) / "signature.der"
        command(runner, ["sbattach", "--detach", str(signature), str(path)])
        certificates = command(runner, [
            "openssl", "pkcs7", "-inform", "DER", "-in", str(signature), "-print_certs",
        ])
        for pem in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", certificates, re.S):
            cert = Path(directory) / "certificate.pem"
            cert.write_text(pem + "\n")
            if runner.run(["sbverify", "--cert", str(cert), str(path)], timeout=30).returncode == 0:
                return
    raise ValueError(f"EFI signature verification failed: {path}")


def payloads(runner: Runner) -> tuple[dict[str, Path], int]:
    architecture = command(runner, ["dpkg", "--print-architecture"]).strip()
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("Unsupported EFI architecture")
    suffix, grub_arch, machine = (
        ("x64", "x86_64", 0x8664) if architecture == "amd64"
        else ("aa64", "arm64", 0xAA64)
    )
    sources = {
        f"shim{suffix}.efi": Path(f"/usr/lib/shim/shim{suffix}.efi.signed.latest").resolve(strict=True),
        f"grub{suffix}.efi": Path(f"/usr/lib/grub/{grub_arch}-efi-signed/grub{suffix}.efi.signed"),
        f"mm{suffix}.efi": Path(f"/usr/lib/shim/mm{suffix}.efi"),
    }
    packages = set()
    for path in sources.values():
        owner = command(runner, ["dpkg-query", "--search", str(path)]).strip()
        if "\n" in owner or ": /" not in owner:
            raise ValueError(f"Ambiguous package ownership: {path}")
        package = owner.split(": /", 1)[0]
        if package.split(":")[0] not in {"shim", "shim-signed", f"grub-efi-{architecture}-signed"}:
            raise ValueError(f"Unexpected EFI payload owner: {path}")
        packages.add(package)
    for package in sorted(packages):
        state = command(runner, ["dpkg-query", "--show", "--showformat=${db:Status-Status}", package])
        if state != "installed" or command(runner, ["dpkg", "--verify", package]).strip():
            raise ValueError(f"EFI package is incomplete or modified: {package}")
    for source in sources.values():
        verify_image(source, machine, runner)
    return sources, machine


def prepare_boot_chain(runner: Runner, esp: Path = ESP) -> None:
    """Called only by the locked privileged helper; no paths come from its CLI."""
    with ExitStack() as stack:
        # Coordinate with APT/dpkg, which use POSIX record locks, not flock.
        for name in ("lock-frontend", "lock"):
            lock = stack.enter_context(open(f"/var/lib/dpkg/{name}", "r+"))
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = resolve_target(runner, esp)
        sources, machine = payloads(runner)
        suffix = "x64" if machine == 0x8664 else "aa64"
        loader = rf"\EFI\AnduinOS\shim{suffix}.efi"
        if target.entry.loader.lower() not in {loader.lower(), loader.lower().replace("shim", "grub")}:
            raise ValueError("Boot entry architecture does not match the installed system")
        vendor = esp / "EFI/AnduinOS"
        if target.entry.loader.lower() == loader.lower() and all(
            (vendor / name).is_file() and not (vendor / name).is_symlink()
            and (vendor / name).read_bytes() == source.read_bytes()
            for name, source in sources.items()
        ):
            # A matching deployed chain has just been verified through the
            # package payloads. MOK preparation needs no EFI writes in this case.
            return
        with tempfile.TemporaryDirectory(prefix=".secureboot-", dir=vendor) as directory:
            staging = Path(directory)
            previous = {}
            for name, source in sources.items():
                destination = vendor / name
                if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                    raise ValueError(f"Unsafe EFI destination: {destination}")
                previous[name] = destination.read_bytes() if destination.exists() else None
                staged = staging / name
                shutil.copyfile(source, staged)
                verify_image(staged, machine, runner)
                with staged.open("rb") as stream:
                    os.fsync(stream.fileno())
            # Recheck identity after verification and before any boot mutation.
            if resolve_target(runner, esp) != target:
                raise ValueError("ESP or firmware boot entries changed during preparation")
            new_number = None
            written = []
            try:
                # Install shim last so its companion files are available first.
                for name in sorted(sources, key=lambda n: n.startswith("shim")):
                    (staging / name).replace(vendor / name)
                    written.append(name)
                descriptor = os.open(vendor, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                if target.entry.loader.lower() != loader.lower():
                    command(runner, ["efibootmgr", "--create-only", "--disk", target.disk,
                                     "--part", str(target.entry.partition), "--label", "AnduinOS", "--loader", loader])
                    updated = command(runner, ["efibootmgr", "--verbose"])
                    old_numbers = {n.upper() for n in re.findall(r"^Boot([0-9A-Fa-f]{4})", target.nvram, re.M)}
                    created = [e for e in entries(updated) if e.number not in old_numbers
                               and e.uuid == target.entry.uuid and e.partition == target.entry.partition
                               and e.loader.lower() == loader.lower()]
                    if len(created) != 1:
                        raise ValueError("Could not verify the new shim boot entry")
                    new_number = created[0].number
                    order = ",".join(new_number if n == target.entry.number else n for n in target.order.split(","))
                    command(runner, ["efibootmgr", "--bootorder", order])
                    if variable(command(runner, ["efibootmgr", "--verbose"]), "BootOrder") != order:
                        raise ValueError("Firmware did not retain the shim boot order")
                    # Keep the old entry as a recovery option, outside BootOrder.
            except Exception as error:
                if new_number:
                    try:
                        command(runner, ["efibootmgr", "--bootorder", target.order])
                        if variable(command(runner, ["efibootmgr", "--verbose"]), "BootOrder") != target.order:
                            raise ValueError("Original BootOrder was not restored")
                        command(runner, ["efibootmgr", "--bootnum", new_number, "--delete-bootnum"])
                    except (OSError, ValueError) as rollback_error:
                        # The new entry may still be selected: retain its verified
                        # files rather than leaving firmware pointing at nothing.
                        raise ValueError(f"{error}; firmware rollback failed: {rollback_error}. Signed files retained; manual review required") from error
                for name in written:
                    if previous[name] is None:
                        (vendor / name).unlink(missing_ok=True)
                    else:
                        backup = staging / name
                        backup.write_bytes(previous[name])
                        backup.replace(vendor / name)
                raise
