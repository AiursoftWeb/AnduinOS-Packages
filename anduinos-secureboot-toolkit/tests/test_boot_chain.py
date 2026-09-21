from contextlib import contextmanager
import json
import shutil
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_secureboot import boot_chain as boot
from anduinos_secureboot import operations


UUID = "12345678-1234-1234-1234-123456789abc"


class Firmware:
    def __init__(self, esp):
        self.esp = esp
        self.order = "0000,0007,0003"
        self.current = "0007"
        self.loader = "grubx64.efi"
        self.new = False
        self.fail_order = False
        self.calls = []
        self.parts = [{"path": "/dev/test1", "pkname": "/dev/test", "type": "part",
                       "parttype": boot.ESP_TYPE, "partuuid": UUID, "partn": 1}]

    def output(self):
        result = f"BootCurrent: {self.current}\nBootOrder: {self.order}\n"
        result += "Boot0000* Windows Boot Manager HD(1,GPT," + UUID + ",0x800,0x10000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n"
        result += f"Boot0007* AnduinOS HD(1,GPT,{UUID},0x800,0x10000)/File(\\EFI\\AnduinOS\\{self.loader})\n"
        if self.new:
            result += f"Boot0009* AnduinOS HD(1,GPT,{UUID},0x800,0x10000)/File(\\EFI\\AnduinOS\\shimx64.efi)\n"
        return result

    def run(self, args, **kwargs):
        self.calls.append(args)
        output = ""
        if args[0] == "findmnt":
            output = json.dumps({"filesystems": [{"source": "/dev/test1", "target": str(self.esp), "fstype": "vfat", "options": "rw,nosuid"}]})
        elif args[0] == "lsblk":
            output = json.dumps({"blockdevices": self.parts})
        elif args == ["efibootmgr", "--verbose"]:
            output = self.output()
        elif args[:2] == ["efibootmgr", "--create-only"]:
            self.new = True
        elif args[:2] == ["efibootmgr", "--bootorder"]:
            if self.fail_order and args[2] != "0000,0007,0003":
                return subprocess.CompletedProcess(args, 1, "", "NVRAM full")
            self.order = args[2]
        elif args[-1] == "--delete-bootnum":
            self.new = False
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, output, "")


class BootChainTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            esp = root / "esp"
            vendor = esp / "EFI/AnduinOS"
            vendor.mkdir(parents=True)
            (vendor / "grub.cfg").write_text("search --fs-uuid root; configfile /boot/grub/grub.cfg")
            sources = {}
            for name in ("shimx64.efi", "grubx64.efi", "mmx64.efi"):
                (vendor / name).write_bytes(b"old " + name.encode())
                source = root / name
                source.write_bytes(b"new " + name.encode())
                sources[name] = source
            for name in ("Microsoft/Boot/bootmgfw.efi", "BOOT/BOOTX64.EFI"):
                path = esp / "EFI" / name
                path.parent.mkdir(parents=True)
                path.write_bytes(b"foreign loader")
            firmware = Firmware(esp)
            # Fake only package/signature and dpkg lock boundaries. All disk
            # selection, staging, file replacement and NVRAM logic executes.
            with (patch.object(boot, "payloads", return_value=(sources, 0x8664)),
                  patch.object(boot, "partition_number", return_value=1),
                  patch.object(boot, "verify_image"),
                  patch.object(boot, "open", side_effect=lambda *a: (root / "lock").open("a+"))):
                yield firmware, esp, vendor

    def test_nvram_parser_accepts_both_efibootmgr_path_formats(self):
        firmware = Firmware(Path("/boot/efi"))
        legacy = firmware.output()
        modern = legacy.replace("File(", "").replace(".efi)", ".efi")
        self.assertEqual(boot.entries(legacy), boot.entries(modern))
        self.assertEqual(len(boot.entries(modern)), 1)

    def test_repair_preserves_foreign_files_and_boot_order_positions(self):
        with self.fixture() as (firmware, esp, vendor):
            boot.prepare_boot_chain(firmware, esp)
            self.assertEqual(firmware.order, "0000,0009,0003")
            self.assertEqual(boot.current_loader(firmware), "shim")
            for name in ("shimx64.efi", "grubx64.efi", "mmx64.efi"):
                self.assertEqual((vendor / name).read_bytes(), b"new " + name.encode())
            for name in ("Microsoft/Boot/bootmgfw.efi", "BOOT/BOOTX64.EFI"):
                self.assertEqual((esp / "EFI" / name).read_bytes(), b"foreign loader")
            # Retrying after boot repair but before a reboot is idempotent too.
            times = {p.name: p.stat().st_mtime_ns for p in vendor.iterdir()}
            boot.prepare_boot_chain(firmware, esp)
            firmware.current = "0009"
            boot.prepare_boot_chain(firmware, esp)
            self.assertEqual(times, {p.name: p.stat().st_mtime_ns for p in vendor.iterdir()})
            self.assertEqual(sum(c[:2] == ["efibootmgr", "--create-only"] for c in firmware.calls), 1)

    def test_failed_nvram_update_restores_vendor_files_and_order(self):
        with self.fixture() as (firmware, esp, vendor):
            firmware.fail_order = True
            with self.assertRaisesRegex(ValueError, "NVRAM full"):
                boot.prepare_boot_chain(firmware, esp)
            self.assertEqual(firmware.order, "0000,0007,0003")
            self.assertFalse(firmware.new)
            self.assertEqual((vendor / "shimx64.efi").read_bytes(), b"old shimx64.efi")

    def test_multiple_esps_fail_before_writes(self):
        with self.fixture() as (firmware, esp, vendor):
            firmware.parts.append({**firmware.parts[0], "path": "/dev/test2"})
            with self.assertRaisesRegex(ValueError, "Multiple"):
                boot.prepare_boot_chain(firmware, esp)
            self.assertFalse(firmware.new)
            self.assertEqual((vendor / "shimx64.efi").read_bytes(), b"old shimx64.efi")

    def test_other_current_boot_entry_is_not_repaired(self):
        with self.fixture() as (firmware, esp, _):
            firmware.current = "0000"
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                boot.prepare_boot_chain(firmware, esp)
            self.assertFalse(firmware.new)

    def test_symlink_and_invalid_signature_fail_before_writes(self):
        with self.fixture() as (firmware, esp, vendor):
            with patch.object(boot, "verify_image", side_effect=ValueError("invalid signature")):
                with self.assertRaisesRegex(ValueError, "invalid signature"):
                    boot.prepare_boot_chain(firmware, esp)
            path = vendor / "shimx64.efi"
            path.unlink()
            path.symlink_to(esp / "EFI/BOOT/BOOTX64.EFI")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                boot.prepare_boot_chain(firmware, esp)
            self.assertEqual(path.read_bytes(), b"foreign loader")
            self.assertFalse(firmware.new)

    def test_setup_mode_reads_efi_attributes_and_rejects_truncated_data(self):
        with tempfile.TemporaryDirectory() as directory:
            efi = Path(directory)
            (efi / "efivars").mkdir()
            var = efi / "efivars" / f"SetupMode-{boot.EFI_GLOBAL}"
            for data, expected in ((b"\x07\0\0\0\1", True), (b"\x07\0\0\0\0", False), (b"\1", None)):
                var.write_bytes(data)
                self.assertIs(boot.setup_mode(efi), expected)

    def test_wrong_pe_architecture_is_rejected_before_signature_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.efi"
            image = bytearray(70)
            image[:2] = b"MZ"
            struct.pack_into("<I", image, 60, 64)
            image[64:68] = b"PE\0\0"
            struct.pack_into("<H", image, 68, 0xAA64)
            path.write_bytes(image)
            with self.assertRaisesRegex(ValueError, "architecture"):
                boot.verify_image(path, 0x8664, None)

    @unittest.skipUnless(Path('/usr/lib/shim/shimx64.efi.signed.latest').is_file()
                         and shutil.which('sbattach') and shutil.which('sbverify'),
                         'requires installed signed amd64 shim and sbsigntool')
    def test_real_signature_detects_modified_executable_body(self):
        from anduinos_secureboot.inspect import SubprocessRunner
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shim.efi'
            shutil.copyfile('/usr/lib/shim/shimx64.efi.signed.latest', path)
            boot.verify_image(path, 0x8664, SubprocessRunner())
            data = bytearray(path.read_bytes())
            pe_offset = struct.unpack_from('<I', data, 60)[0]
            optional_size = struct.unpack_from('<H', data, pe_offset + 20)[0]
            section = pe_offset + 24 + optional_size
            body = struct.unpack_from('<I', data, section + 20)[0]
            data[body + 16] ^= 1
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                boot.verify_image(path, 0x8664, SubprocessRunner())

    def test_boot_failure_prevents_mok_mutation(self):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "SecureBoot disabled\n", "")
        with (patch.object(boot, "prepare_boot_chain", side_effect=ValueError("ambiguous ESP")),
              patch.object(operations, "prepare") as prepare):
            result = operations.execute("prepare", run)
            self.assertFalse(result.ok)
            self.assertEqual(result.steps["boot_chain"].status, "failed")
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
