import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


VM_DIR = Path(__file__).parent / "vm"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "vm_runner", VM_DIR / "run-qemu.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VmMatrixTests(unittest.TestCase):
    def test_matrix_covers_every_release_one_combination(self):
        matrix = json.loads((VM_DIR / "matrix.json").read_text())
        actual = {
            (
                case["architecture"],
                case["firmware"],
                case["secure_boot"],
                case["filesystem"],
            )
            for case in matrix["cases"]
        }
        expected = {
            ("amd64", "bios", False, filesystem)
            for filesystem in ("btrfs", "ext4")
        }
        expected |= {
            (architecture, "uefi", secure_boot, filesystem)
            for architecture in ("amd64", "arm64")
            for secure_boot in (False, True)
            for filesystem in ("btrfs", "ext4")
        }
        self.assertEqual(actual, expected)
        self.assertEqual(len(matrix["cases"]), len(actual))
        self.assertGreaterEqual(matrix["disk_gib"], 25)

    def test_runner_uses_only_a_fresh_qcow_target(self):
        runner = load_runner()
        matrix, case = runner.load_case("amd64-bios-btrfs")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            args = SimpleNamespace(
                output=output,
                iso=Path("/tmp/installer.iso"),
                uefi_code=None,
                uefi_vars=None,
            )
            disk = output / "target.qcow2"
            command = runner.build_command(
                args, matrix, case, disk, output / "uefi-vars.fd"
            )
        drive = next(
            argument for argument in command if "id=target" in argument
        )
        self.assertIn("format=qcow2", drive)
        self.assertIn(str(disk), drive)
        self.assertFalse(any("/dev/" in argument for argument in command))

    def test_secure_boot_case_requires_explicit_firmware_pair(self):
        runner = load_runner()
        matrix, case = runner.load_case("amd64-secureboot-btrfs")
        args = SimpleNamespace(
            output=Path("/tmp/vm-output"),
            iso=Path("/tmp/installer.iso"),
            uefi_code=None,
            uefi_vars=None,
        )
        with self.assertRaisesRegex(SystemExit, "require"):
            runner.build_command(
                args,
                matrix,
                case,
                args.output / "target.qcow2",
                args.output / "uefi-vars.fd",
            )

