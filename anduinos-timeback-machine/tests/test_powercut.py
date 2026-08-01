import importlib.util
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest


MODULE_PATH = Path(__file__).parent / "vm" / "powercut.py"
SPEC = importlib.util.spec_from_file_location("tm5_powercut", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
powercut = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(powercut)


class FakeProcess:
    returncode = None

    def poll(self):
        return self.returncode


class PowerCutControllerTests(unittest.TestCase):
    def test_matrix_covers_every_apply_and_revert_boundary(self):
        self.assertEqual(len(powercut.APPLY_CHECKPOINTS), 5)
        self.assertEqual(len(powercut.REVERT_CHECKPOINTS), 5)
        self.assertEqual(len(set(powercut.ALL_CHECKPOINTS)), 10)

    def test_qemu_command_uses_only_the_overlay_as_a_system_disk(self):
        arguments = SimpleNamespace(
            qemu_system="/usr/bin/qemu-system-test",
            machine="q35,accel=tcg",
            cpu="max",
            memory_mib=4096,
            cpus=2,
            uefi_code=None,
        )
        command = powercut.qemu_command(
            arguments,
            Path("/qualification/scenario/disk.qcow2"),
            Path("/qualification/scenario/serial.sock"),
            Path("/qualification/scenario/qmp.sock"),
            None,
        )
        drive = command[command.index("-drive") + 1]
        self.assertIn("file=/qualification/scenario/disk.qcow2", drive)
        self.assertNotIn("fixture", " ".join(command))
        self.assertIn("name=opt/anduinos/timeback-expected,string=fallback", command)

    def test_serial_checkpoint_is_consumed_without_polling_a_log_file(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        writer.sendall(b"booting\nTIMEBACK-CHECKPOINT current-root-protected\n")
        output = Path(self.id().replace(".", "-") + ".serial-test")
        self.addCleanup(output.unlink, missing_ok=True)
        powercut.wait_for_serial(
            FakeProcess(),
            reader,
            output,
            "TIMEBACK-CHECKPOINT current-root-protected",
            1,
        )
        self.assertIn(b"current-root-protected", output.read_bytes())

    def test_qemu_key_value_paths_reject_option_injection(self):
        with self.assertRaises(powercut.QualificationError):
            powercut.qemu_keyval_path(Path("/tmp/disk,readonly=off"), "test path")


if __name__ == "__main__":
    unittest.main()
