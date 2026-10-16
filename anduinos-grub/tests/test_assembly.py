import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("assembly", ROOT / "assemble-efi.py")
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def pe_fixture(machine=0x8664, signed=False):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<HH", data, 68, machine, 1)
    struct.pack_into("<H", data, 84, 240)
    struct.pack_into("<H", data, 88, 0x20B)
    if signed:
        struct.pack_into("<II", data, 88 + 112 + 32, 900, 8)
    data[328:336] = b".sbat\0\0\0"
    struct.pack_into("<II", data, 328 + 16, 4, 512)
    data[512:516] = b"test"
    return bytes(data)


def module_fixture(name):
    data = bytearray(512)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", data, 40, 64)
    struct.pack_into("<HHH", data, 58, 64, 2, 0)
    strings = b"\0.shstrtab\0.modname\0"
    struct.pack_into("<IIQQQQIIQQ", data, 64, 1, 3, 0, 0, 256, len(strings), 0, 0, 1, 0)
    encoded = name.encode() + b"\0"
    struct.pack_into("<IIQQQQIIQQ", data, 128, 11, 1, 0, 0, 320, len(encoded), 0, 0, 1, 0)
    data[256:256 + len(strings)] = strings
    data[320:320 + len(encoded)] = encoded
    return bytes(data)


class AssemblyTests(unittest.TestCase):
    def test_update_hook_accepts_only_the_native_efi_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dpkg = root / "dpkg"
            dpkg.write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_ARCH"\n')
            dpkg.chmod(0o755)
            update = root / "update"
            update.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\n')
            update.chmod(0o755)
            hook = root / "hook"
            # Redirect only the privileged update executable into the fixture.
            hook.write_text((ROOT / "scripts/grub-multi-install").read_text().replace(
                "/usr/libexec/anduinos-grub-update", str(update)))
            for arch, target, other in (("amd64", "x86_64-efi", "arm64-efi"),
                                         ("arm64", "arm64-efi", "x86_64-efi")):
                environment = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", TEST_ARCH=arch)
                for arguments in ([], [f"--target={target}"]):
                    with self.subTest(arch=arch, arguments=arguments):
                        result = subprocess.run(["sh", str(hook), *arguments], env=environment,
                                                text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.strip(), "deploy")
                for arguments in ([f"--target={other}"], ["--target=i386-pc"],
                                  [f"--target={target}", "--force"]):
                    result = subprocess.run(["sh", str(hook), *arguments], env=environment,
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 64)
                    self.assertEqual(result.stdout, "")
            environment["TEST_ARCH"] = "riscv64"
            self.assertEqual(subprocess.run(["sh", str(hook)], env=environment,
                                            capture_output=True).returncode, 64)

    def test_reads_both_pe_architectures(self):
        for machine in (0x8664, 0xAA64):
            self.assertEqual(assembly.pe_sections(pe_fixture(machine)),
                             (machine, {".sbat": b"test"}))

    def test_signed_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsigned"):
            assembly.pe_sections(pe_fixture(signed=True))

    def test_truncated_pe_fails_closed(self):
        for size in (0, 2, 60, 90, 350, 513):
            with self.assertRaises(ValueError):
                assembly.pe_sections(pe_fixture()[:size])

    def test_elf_module_names(self):
        for name in ("btrfs", "luks2", "argon2", "peimage"):
            self.assertEqual(assembly.elf_module_name(module_fixture(name)), name)
        with self.assertRaises(ValueError):
            assembly.elf_module_name(module_fixture("../bad"))

    def test_module_table(self):
        body = struct.pack("<II", 1, 16) + b"12345678"
        table = struct.pack("<IIQQ", 0x676D696D, 0, 24, 24 + len(body)) + body
        self.assertEqual(assembly.grub_objects(table), [(1, b"12345678")])
        with self.assertRaises(ValueError):
            assembly.grub_objects(table[:-1])
        malformed = bytearray(table)
        struct.pack_into("<I", malformed, 28, 0)
        with self.assertRaises(ValueError):
            assembly.grub_objects(bytes(malformed))

    def test_sbat_preserves_upstream_generations_without_version_pins(self):
        baseline = ("sbat,1,SBAT,sbat,1,https://example.test/\n"
                    "grub,99,GNU,grub,88.7,https://example.test/\n"
                    "grub.ubuntu,42,Ubuntu,grub2,88.7-9ubuntu6,https://example.test/\n"
                    "grub.peimage,8,Canonical,grub2,88.7-9ubuntu6,https://example.test/\n")
        result = assembly.sbat_with_identity(baseline.encode() + b"\0" * 16, "88.7-9ubuntu6")
        self.assertTrue(result.startswith(baseline))
        self.assertIn("grub.anduinos,1,AnduinOS,anduinos-grub,88.7-9ubuntu6,", result)
        with self.assertRaises(ValueError):
            assembly.sbat_with_identity(b"grub,1,missing,columns", "unused")
        with self.assertRaises(ValueError):
            assembly.sbat_with_identity(result.encode(), "unused")

    def test_project_inputs_follow_suite_and_keep_both_native_tool_payloads(self):
        project = ET.parse(ROOT / "anduinos-grub.aosproj")
        self.assertEqual(project.findtext(".//TargetArchitectures").split(), ["amd64", "arm64"])
        inputs = {node.get("Id"): node for node in project.findall(".//BuildInput")}
        for arch in ("amd64", "arm64"):
            self.assertEqual(inputs["tools-" + arch].get("Arch"), arch)
        for source in project.findall(".//BuildSource"):
            self.assertEqual(source.get("Suites"), "$(SuiteShortName) $(SuiteShortName)-updates $(SuiteShortName)-security")
        self.assertEqual(inputs["bios"].get("Condition"), "'$(Arch)' == 'amd64'")
        self.assertFalse(project.findall(".//IncludeFolder"), "Do not package a shared/stale deploy tree")


if __name__ == "__main__":
    unittest.main()
