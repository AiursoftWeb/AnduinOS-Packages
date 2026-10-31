"""Reassemble Ubuntu's EFI baseline with encryption modules, on either host ISA.

Only reads PE/ELF metadata; grub-mkimage performs all linking. Keeping the actual
Ubuntu module list, memdisk and SBAT avoids a version-pinned duplicate recipe.
"""
import csv
import io
from pathlib import Path
import re
import struct
import subprocess
import sys


def region(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError("Truncated binary metadata")
    return data[offset:offset + size]


def pe_sections(data):
    if data[:2] != b"MZ":
        raise ValueError("Expected a PE image")
    offset, = struct.unpack("<I", region(data, 60, 4))
    if region(data, offset, 4) != b"PE\0\0":
        raise ValueError("Invalid PE signature")
    machine, count = struct.unpack("<HH", region(data, offset + 4, 4))
    optional_size, = struct.unpack("<H", region(data, offset + 20, 2))
    optional = region(data, offset + 24, optional_size)
    if optional[:2] != b"\x0b\x02":
        raise ValueError("Expected PE32+")
    # Security directory holds a file offset, not an RVA. Templates stay unsigned.
    if any(region(optional, 112 + 4 * 8, 8)):
        raise ValueError("Expected an unsigned GRUB template")
    sections = {}
    for index in range(count):
        header = region(data, offset + 24 + optional_size + index * 40, 40)
        name = header[:8].rstrip(b"\0").decode("ascii")
        size, address = struct.unpack_from("<II", header, 16)
        if name in sections:
            raise ValueError("Duplicate PE section")
        sections[name] = region(data, address, size)
    return machine, sections


def elf_module_name(data):
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected ELF64 little-endian module")
    offset, = struct.unpack("<Q", region(data, 40, 8))
    size, count, names_index = struct.unpack("<HHH", region(data, 58, 6))
    if size != 64 or names_index >= count:
        raise ValueError("Invalid ELF section table")
    sections = [struct.unpack("<IIQQQQIIQQ", region(data, offset + i * size, size))
                for i in range(count)]
    names = region(data, sections[names_index][4], sections[names_index][5])
    for section in sections:
        if names[section[0]:].split(b"\0", 1)[0] == b".modname":
            name = region(data, section[4], section[5]).split(b"\0", 1)[0].decode("ascii")
            if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
                raise ValueError("Invalid GRUB module name")
            return name
    raise ValueError("Missing ELF .modname")


def grub_objects(data):
    magic, _, offset, end = struct.unpack("<IIQQ", region(data, 0, 24))
    if magic != 0x676D696D or not 24 <= offset <= end <= len(data):
        raise ValueError("Invalid GRUB module table")
    objects = []
    while offset < end:
        kind, size = struct.unpack("<II", region(data, offset, 8))
        if size < 8 or offset + size > end:
            raise ValueError("Invalid GRUB object length")
        objects.append((kind, region(data, offset + 8, size - 8)))
        offset += (size + 7) & ~7
    if offset != end:
        raise ValueError("Invalid GRUB module alignment")
    return objects


def sbat_with_identity(data, module_version):
    rows = list(csv.reader(io.StringIO(data.rstrip(b"\0").decode("utf-8"))))
    if any(len(row) != 6 for row in rows):
        raise ValueError("Invalid upstream SBAT")
    identities = {row[0] for row in rows}
    if not {"sbat", "grub", "grub.peimage"} <= identities or "grub.anduinos" in identities:
        raise ValueError("Unexpected upstream SBAT identities")
    # Preserve ALL upstream revocation identities/generations; add our own.
    # Generation 1 is a revocation counter, not a pinned Ubuntu version.
    rows.append(["grub.anduinos", "1", "AnduinOS", "anduinos-grub",
                 module_version, "https://www.anduinos.com/"])
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue()


def assemble(mkimage, modules, baseline, config, output, work):
    modules, baseline, config, output, work = map(Path, (modules, baseline, config, output, work))
    platform = modules.name
    machine = {"x86_64-efi": 0x8664, "arm64-efi": 0xAA64}[platform]
    base_machine, sections = pe_sections(baseline.read_bytes())
    if base_machine != machine:
        raise ValueError("Baseline architecture does not match modules")
    objects = grub_objects(sections["mods"])
    if any(kind not in (0, 1, 3) for kind, _ in objects):
        raise ValueError("New upstream embedded object type: review before repacking")
    base_names = {elf_module_name(data) for kind, data in objects if kind == 0}
    names = base_names | {"btrfs", "luks2", "argon2"}
    if "peimage" not in base_names:
        raise ValueError("Missing Ubuntu shim verification module")
    for name in names:
        if not (modules / (name + ".mod")).is_file():
            raise ValueError(f"Required module is missing: {name}")
    modinfo = (modules / "modinfo.sh").read_text()
    version = re.search(r'^grub_package_version="([^"]+)"$', modinfo, re.M).group(1)
    upstream = re.search(r'^grub_version="([^"]+)"$', modinfo, re.M).group(1)
    tool_version = subprocess.check_output([mkimage, "--version"], text=True).strip()
    # Ubuntu builds grub2 tools and grub2-unsigned EFI modules as separate source
    # packages. Debian revisions differ legitimately; require the same GRUB release.
    if tool_version.rsplit(" ", 1)[-1].rsplit("-", 1)[0] != upstream:
        raise ValueError(f"GRUB tool/module release mismatch: {tool_version} / {version}")
    work.mkdir(parents=True, exist_ok=True)
    sbat = work / "sbat.csv"
    sbat.write_text(sbat_with_identity(sections[".sbat"], version))
    command = [mkimage, "-O", platform, "-d", str(modules), "-p", "/EFI/AnduinOS",
               "-c", str(config), "--sbat", str(sbat), "-o", str(output)]
    memdisks = [data for kind, data in objects if kind == 1]
    if len(memdisks) != 1:
        raise ValueError("Expected one upstream font memdisk")
    memdisk = work / "memdisk.img"
    memdisk.write_bytes(memdisks[0])
    command += ["-m", str(memdisk), *sorted(names)]
    subprocess.run(command, check=True)
    actual_machine, actual = pe_sections(output.read_bytes())
    actual_objects = grub_objects(actual["mods"])
    actual_names = {elf_module_name(data) for kind, data in actual_objects if kind == 0}
    if actual_machine != machine or not names <= actual_names:
        raise ValueError("Assembled EFI lost required architecture/modules")
    if [data for kind, data in actual_objects if kind == 1] != memdisks:
        raise ValueError("Assembled EFI lost the upstream memdisk")
    if [data.rstrip(b"\0") for kind, data in actual_objects if kind == 2] != [config.read_bytes().rstrip(b"\0")]:
        raise ValueError("Assembled EFI lost the AnduinOS bootstrap config")
    if actual[".sbat"].rstrip(b"\0") != sbat.read_bytes():
        raise ValueError("Assembled EFI lost SBAT metadata")
    (work / "modules.txt").write_text("\n".join(sorted(actual_names)) + "\n")
    print(f"{platform}: {len(actual_names)} modules; unsigned; tools {tool_version}; modules {version}")


if __name__ == "__main__":
    assemble(*sys.argv[1:])
