import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from anduinos_rescue_center.boot import Esp, _boot_files, _efi_spec, _locate_esp, _nvram_entry, diagnose_boot, repair_boot
from anduinos_rescue_center.model import Partition


IDENTITY = "a" * 64
EFI_IDENTITY = "b" * 64


def partition() -> Partition:
    return Partition(
        path="/dev/sda2", parent="/dev/sda", size_bytes=10**10,
        filesystem="ext4", filesystem_uuid="root-uuid", label="",
        partuuid="root-part", mountpoints=(), identity=IDENTITY,
    )


def prepare_root(root: Path) -> None:
    (root / "usr/lib").mkdir(parents=True)
    (root / "usr/lib/os-release").write_text("ID=anduinos\n", encoding="utf-8")
    (root / "etc").mkdir()
    (root / "etc/fstab").write_text(
        "UUID=1234-ABCD /boot/efi vfat defaults 0 1\n", encoding="utf-8"
    )
    (root / "boot/grub").mkdir(parents=True)
    (root / "boot/vmlinuz-7.0-test").write_bytes(b"kernel")
    (root / "boot/initrd.img-7.0-test").write_bytes(b"initrd")
    (root / "boot/grub/grub.cfg").write_text(
        "menuentry AnduinOS {\n linux /vmlinuz-7.0-test root=UUID=root\n"
        " initrd /initrd.img-7.0-test\n}\n",
        encoding="utf-8",
    )


class BootRepairTests(unittest.TestCase):
    def test_grub_reference_in_comment_is_not_a_boot_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_root(root)
            (root / "boot/grub/grub.cfg").write_text(
                "# linux /vmlinuz-7.0-test\n# initrd /initrd.img-7.0-test\n",
                encoding="utf-8",
            )
            self.assertFalse(_boot_files(root)[2])

    def test_firmware_entry_must_be_active_ordered_and_match_exact_loader(self):
        esp = Esp("/dev/sda1", EFI_IDENTITY, "1234-ABCD", 1, "/dev/sda", False)

        def check(output):
            def run(command, **_kwargs):
                return subprocess.CompletedProcess(command, 0, output, "")
            return _nvram_entry(esp, run=run)

        valid = ("BootOrder: 0002,0001\n"
                 "Boot0002* AnduinOS HD(1,GPT,1234-ABCD,0x800,0x10000)/"
                 "File(\\EFI\\AnduinOS\\shimx64.efi)\n")
        with patch("anduinos_rescue_center.boot.platform.machine", return_value="x86_64"):
            self.assertTrue(check(valid))
            self.assertFalse(check(valid.replace("shimx64.efi", "grubx64.efi")))
            self.assertIsNone(check(valid.replace("Boot0002*", "Boot0002")))
            self.assertIsNone(check(valid.replace("0002,0001", "0001")))

    def test_efi_partition_must_match_fstab_on_selected_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_root(root)
            self.assertEqual(_efi_spec(root), ("uuid", "1234-abcd"))

        tree = {"blockdevices": [
            {"path": "/dev/sdb", "children": [
                {"path": "/dev/sdb1", "type": "part", "uuid": "1234-ABCD",
                 "fstype": "vfat", "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                 "partn": 1, "partuuid": "wrong-disk"},
            ]},
            {"path": "/dev/sda", "children": [
                {"path": "/dev/sda1", "type": "part", "uuid": "1234-ABCD",
                 "fstype": "vfat", "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                 "partn": 1, "partuuid": "correct-disk",
                 "maj:min": "8:1", "size": 104857600, "mountpoints": []},
            ]},
        ]}

        def run(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, json.dumps(tree), "")

        esp = _locate_esp(partition(), ("uuid", "1234-abcd"), run=run)
        self.assertEqual(esp.path, "/dev/sda1")
        self.assertEqual(esp.partuuid, "correct-disk")
        tree["blockdevices"][1]["children"][0]["parttype"] = "microsoft-basic-data"
        with self.assertRaisesRegex(RuntimeError, "not marked as an EFI"):
            _locate_esp(partition(), ("uuid", "1234-abcd"), run=run)
        tree["blockdevices"][1]["children"][0]["parttype"] = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
        tree["blockdevices"][1]["children"] = []
        with self.assertRaisesRegex(RuntimeError, "selected disk"):
            _locate_esp(partition(), ("uuid", "1234-abcd"), run=run)

    def test_diagnosis_reads_kernel_grub_efi_and_firmware_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory) / "root"
            efi = Path(directory) / "efi"
            top.mkdir()
            (efi / "EFI/AnduinOS").mkdir(parents=True)
            (efi / "EFI/AnduinOS/shimx64.efi").write_bytes(b"shim")
            (efi / "EFI/AnduinOS/grubx64.efi").write_bytes(b"grub")
            (efi / "EFI/AnduinOS/grub.cfg").write_bytes(b"config")
            prepare_root(top)
            esp = Esp("/dev/sda1", EFI_IDENTITY, "1234-ABCD", 1, "/dev/sda", False)
            seen = []

            @contextmanager
            def readonly(path, _filesystem, **_kwargs):
                seen.append(path)
                yield top if path == "/dev/sda2" else efi

            with (patch("anduinos_rescue_center.boot.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.boot.mounted_readonly", readonly),
                  patch("anduinos_rescue_center.boot._locate_esp", return_value=esp),
                  patch("anduinos_rescue_center.boot._nvram_entry", return_value=True),
                  patch("anduinos_rescue_center.boot._firmware_available", return_value=True)):
                report = diagnose_boot("/dev/sda2", IDENTITY)
            self.assertEqual(seen, ["/dev/sda2", "/dev/sda1"])
            self.assertEqual(report["complete_pairs"], 1)
            self.assertTrue(report["grub_config"])
            self.assertTrue(report["efi_loader"])
            self.assertEqual(report["issues"], [])

    def test_repair_refuses_changed_efi_identity_before_any_write(self):
        with patch("anduinos_rescue_center.boot.diagnose_boot", return_value={
            "repairable": True, "esp_identity": EFI_IDENTITY,
        }), patch("anduinos_rescue_center.boot.opened_system") as opened:
            with self.assertRaisesRegex(RuntimeError, "changed"):
                repair_boot("/dev/sda2", IDENTITY, "c" * 64)
            opened.assert_not_called()

    def test_repair_runs_vendor_only_grub_install_and_verifies_result(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory) / "root"
            top.mkdir()
            prepare_root(top)
            for tool in ("usr/bin/dracut", "usr/bin/lsinitrd", "usr/sbin/grub-install", "usr/sbin/update-grub"):
                path = top / tool
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"tool")
            shim = top / "boot/efi/EFI/AnduinOS/shimx64.efi"
            shim.parent.mkdir(parents=True)
            shim.write_bytes(b"shim")
            (shim.parent / "grubx64.efi").write_bytes(b"grub")
            (shim.parent / "grub.cfg").write_bytes(b"config")
            esp = Esp("/dev/sda1", EFI_IDENTITY, "1234-ABCD", 1, "/dev/sda", False)
            commands = []

            @contextmanager
            def readonly(_path, _filesystem, **_kwargs):
                yield top

            @contextmanager
            def opened(*_args, **_kwargs):
                yield top, partition()

            def run(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            report = {"repairable": True, "esp_identity": EFI_IDENTITY,
                      "nvram_entry": True, "esp": "/dev/sda1"}
            with (patch("anduinos_rescue_center.boot.diagnose_boot", return_value=report),
                  patch("anduinos_rescue_center.boot.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.boot.mounted_readonly", readonly),
                  patch("anduinos_rescue_center.boot._locate_esp", return_value=esp),
                  patch("anduinos_rescue_center.boot.opened_system", opened)):
                repair_boot("/dev/sda2", IDENTITY, EFI_IDENTITY, run=run)
            self.assertEqual(len(commands), 4)
            self.assertEqual(commands[1][2:4], ["lsinitrd", "-m"])
            self.assertIn("--no-extra-removable", commands[2])
            self.assertIn("--no-nvram", commands[2])
            self.assertNotIn("EFI/BOOT", " ".join(" ".join(item) for item in commands))

    def test_missing_firmware_entry_is_created_only_on_selected_efi_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory) / "root"
            top.mkdir()
            prepare_root(top)
            for tool in ("usr/bin/dracut", "usr/bin/lsinitrd", "usr/sbin/grub-install", "usr/sbin/update-grub"):
                path = top / tool
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"tool")
            vendor = top / "boot/efi/EFI/AnduinOS"
            vendor.mkdir(parents=True)
            for name in ("shimx64.efi", "grubx64.efi", "grub.cfg"):
                (vendor / name).write_bytes(b"boot")
            esp = Esp("/dev/sda1", EFI_IDENTITY, "1234-ABCD", 1, "/dev/sda", False)
            calls = []

            @contextmanager
            def readonly(_path, _filesystem, **_kwargs):
                yield top

            @contextmanager
            def opened(*_args, **_kwargs):
                yield top, partition()

            def run(command, **_kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            report = {"repairable": True, "esp_identity": EFI_IDENTITY,
                      "nvram_entry": False, "esp": "/dev/sda1"}
            with (patch("anduinos_rescue_center.boot.diagnose_boot", return_value=report),
                  patch("anduinos_rescue_center.boot.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.boot.mounted_readonly", readonly),
                  patch("anduinos_rescue_center.boot._locate_esp", return_value=esp),
                  patch("anduinos_rescue_center.boot._firmware_available", return_value=True),
                  patch("anduinos_rescue_center.boot.opened_system", opened)):
                repair_boot("/dev/sda2", IDENTITY, EFI_IDENTITY, run=run)
            self.assertEqual(calls[4][:7],
                             ["efibootmgr", "--create", "--disk", "/dev/sda",
                              "--part", "1", "--label"])
            self.assertEqual(calls[4][-2:], ["--loader", r"\EFI\AnduinOS\shimx64.efi"])

    def test_live_modules_in_rebuilt_initrd_stop_repair_before_grub_write(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            prepare_root(top)
            for tool in ("usr/bin/dracut", "usr/bin/lsinitrd", "usr/sbin/grub-install", "usr/sbin/update-grub"):
                path = top / tool
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"tool")
            esp = Esp("/dev/sda1", EFI_IDENTITY, "1234-ABCD", 1, "/dev/sda", False)
            commands = []

            @contextmanager
            def readonly(_path, _filesystem, **_kwargs):
                yield top

            @contextmanager
            def opened(*_args, **_kwargs):
                yield top, partition()

            def run(command, **_kwargs):
                commands.append(command)
                output = "dmsquash-live\n" if "lsinitrd" in command else ""
                return subprocess.CompletedProcess(command, 0, output, "")

            report = {"repairable": True, "esp_identity": EFI_IDENTITY, "nvram_entry": True}
            with (patch("anduinos_rescue_center.boot.diagnose_boot", return_value=report),
                  patch("anduinos_rescue_center.boot.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.boot.mounted_readonly", readonly),
                  patch("anduinos_rescue_center.boot._locate_esp", return_value=esp),
                  patch("anduinos_rescue_center.boot.opened_system", opened)):
                with self.assertRaisesRegex(RuntimeError, "Live boot modules"):
                    repair_boot("/dev/sda2", IDENTITY, EFI_IDENTITY, run=run)
            self.assertEqual([command[2] for command in commands], ["dracut", "lsinitrd"])

    def test_diagnosis_disables_repair_when_firmware_variables_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory) / "root"
            efi = Path(directory) / "efi"
            top.mkdir()
            efi.mkdir()
            prepare_root(top)
            esp = Esp("/dev/sda1", EFI_IDENTITY, "1234-ABCD", 1, "/dev/sda", False)

            @contextmanager
            def readonly(path, _filesystem, **_kwargs):
                yield top if path == "/dev/sda2" else efi

            with (patch("anduinos_rescue_center.boot.resolve_target", return_value=partition()),
                  patch("anduinos_rescue_center.boot.mounted_readonly", readonly),
                  patch("anduinos_rescue_center.boot._locate_esp", return_value=esp),
                  patch("anduinos_rescue_center.boot._nvram_entry", return_value=False),
                  patch("anduinos_rescue_center.boot._firmware_available", return_value=False)):
                report = diagnose_boot("/dev/sda2", IDENTITY)
            self.assertFalse(report["repairable"])
            self.assertTrue(any("UEFI variables" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
