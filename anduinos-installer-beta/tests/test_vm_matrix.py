import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


VM_DIR = Path(__file__).parent / "vm"


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "vm_runner", VM_DIR / "run-qemu.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_coexistence_runner():
    spec = importlib.util.spec_from_file_location(
        "coexistence_vm_runner", VM_DIR / "run-coexistence-qemu.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_coexistence_verifier():
    spec = importlib.util.spec_from_file_location(
        "coexistence_artifact_verifier",
        VM_DIR / "verify-coexistence-artifacts.py",
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

    def test_coexistence_matrix_covers_filesystem_boot_and_esp_policy(self):
        matrix = json.loads(
            (VM_DIR / "coexistence-matrix.json").read_text()
        )
        actual = {
            (
                case["secure_boot"],
                case["filesystem"],
                case["esp_policy"],
            )
            for case in matrix["cases"]
        }
        expected = {
            (secure_boot, filesystem, esp_policy)
            for secure_boot in (False, True)
            for filesystem in ("btrfs", "ext4")
            for esp_policy in ("reuse", "new")
        }
        self.assertEqual(actual, expected)
        self.assertTrue(
            all(case["firmware"] == "uefi" for case in matrix["cases"])
        )

    def test_coexistence_runner_uses_only_cloned_qcow_fixture(self):
        runner = load_coexistence_runner()
        matrix, case = runner.load_case("amd64-uefi-btrfs-shared-esp")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            args = SimpleNamespace(
                output=output,
                iso=Path("/tmp/installer.iso"),
                fixture=Path("/tmp/windows-fixture.qcow2"),
                uefi_code=Path("/tmp/OVMF_CODE.fd"),
                uefi_vars=Path("/tmp/OVMF_VARS.fd"),
            )
            disk = output / "coexistence-target.qcow2"
            evidence_disk = output / "evidence.qcow2"
            command = runner.build_command(
                args,
                matrix,
                case,
                disk,
                output / "uefi-vars.fd",
                evidence_disk,
            )
        drive = next(
            argument for argument in command if "id=target" in argument
        )
        self.assertIn(str(disk), drive)
        self.assertIn("format=qcow2", drive)
        self.assertTrue(
            any(
                "serial=ANDUINOS-COEXISTENCE-TARGET" in argument
                for argument in command
            )
        )
        artifact_drive = next(
            argument for argument in command if "id=evidence" in argument
        )
        self.assertIn(str(evidence_disk), artifact_drive)
        self.assertIn("format=qcow2", artifact_drive)
        self.assertTrue(
            any(
                "serial=ANDUINOS-EVIDENCE" in argument
                for argument in command
            )
        )
        self.assertFalse(any("/dev/" in argument for argument in command))

    def test_coexistence_resume_requires_a_matching_power_cut_campaign(self):
        runner = load_coexistence_runner()
        _matrix, case = runner.load_case(
            "amd64-uefi-btrfs-shared-esp"
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            args = SimpleNamespace(
                iso=Path("/tmp/installer.iso"),
                fixture=Path("/tmp/windows-fixture.qcow2"),
                uefi_code=Path("/tmp/OVMF_CODE.fd"),
                uefi_vars=Path("/tmp/windows-vars.fd"),
            )
            disk = output / "coexistence-target.qcow2"
            variables = output / "uefi-vars.fd"
            evidence = output / "evidence.qcow2"
            for path in (disk, variables, evidence):
                path.write_bytes(b"fixture")
            metadata = {
                **case,
                "iso": str(args.iso),
                "fixture": str(args.fixture),
                "uefi_code": str(args.uefi_code),
                "uefi_vars": str(args.uefi_vars),
                "fixture_sha256": "a" * 64,
                "iso_sha256": "b" * 64,
                "uefi_code_sha256": "c" * 64,
                "uefi_vars_sha256": "d" * 64,
                "executor_policy": "guided-destructive-test",
                "power_cut_marker": (
                    "[anduinos-boundary:guided-format-root:after]"
                ),
                "evidence_disk": str(evidence),
            }
            (output / "case.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(SystemExit, "power-cut record"):
                runner.validate_resume_campaign(
                    output,
                    args,
                    case,
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    "d" * 64,
                    disk,
                    variables,
                    evidence,
                )
            (output / "power-cut.json").write_text(
                json.dumps(
                    {
                        "marker": metadata["power_cut_marker"],
                        "triggered": True,
                    }
                )
            )
            runner.validate_resume_campaign(
                output,
                args,
                case,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                disk,
                variables,
                evidence,
            )
            metadata["fixture_sha256"] = "b" * 64
            (output / "case.json").write_text(json.dumps(metadata))
            with self.assertRaisesRegex(SystemExit, "does not match"):
                runner.validate_resume_campaign(
                    output,
                    args,
                    case,
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    "d" * 64,
                    disk,
                    variables,
                    evidence,
                )

    def test_coexistence_fixture_requires_exact_sha256(self):
        runner = load_coexistence_runner()
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.qcow2"
            fixture.write_bytes(b"fixture")
            actual = runner.sha256_file(fixture)
            self.assertEqual(runner.verify_fixture(fixture, actual), actual)
            self.assertEqual(
                runner.verify_file(fixture, actual, "ISO"), actual
            )
            with self.assertRaisesRegex(SystemExit, "mismatch"):
                runner.verify_fixture(fixture, "0" * 64)
        with self.assertRaisesRegex(SystemExit, "not a regular file"):
            runner.require_regular_file(Path("/dev/null"), "Fixture")

    def test_coexistence_power_cut_waits_for_exact_serial_marker(self):
        runner = load_coexistence_runner()
        marker = runner.power_cut_marker(
            "guided-format-root:after"
        )
        self.assertEqual(
            marker,
            "[anduinos-boundary:guided-format-root:after]",
        )
        with self.assertRaisesRegex(SystemExit, "must be"):
            runner.power_cut_marker("/dev/vda:after")

        with tempfile.TemporaryDirectory() as directory:
            serial = Path(directory) / "serial.log"

            class FakeProcess:
                killed = False

                def poll(self):
                    serial.write_text("prefix " + marker + " suffix")
                    return None

                def kill(self):
                    self.killed = True

                def wait(self):
                    return -9

            process = FakeProcess()
            with (
                patch.object(
                    runner.subprocess, "Popen", return_value=process
                ),
                patch.object(runner.time, "sleep"),
            ):
                triggered = runner.run_until_power_cut(
                    ["qemu-test"], serial, marker, 1
                )
            self.assertTrue(triggered)
            self.assertTrue(process.killed)

    def test_run_result_never_claims_automatic_pass(self):
        runner = load_coexistence_runner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            disk = output / "target.qcow2"
            variables = output / "vars.fd"
            evidence = output / "evidence.qcow2"
            disk.write_bytes(b"target")
            variables.write_bytes(b"vars")
            evidence.write_bytes(b"evidence")
            runner.write_run_result(
                output,
                mode="normal",
                returncode=0,
                started_at="2026-01-01T00:00:00+00:00",
                disk=disk,
                vars_copy=variables,
                evidence_disk=evidence,
            )
            result = json.loads((output / "run-result.json").read_text())
            self.assertIsNone(result["test_passed"])
            self.assertTrue(result["requires_manual_review"])
            self.assertEqual(
                result["artifacts"]["target_sha256"],
                runner.sha256_file(disk),
            )

    def test_campaign_artifact_verifier_detects_post_run_mutation(self):
        runner = load_coexistence_runner()
        verifier = load_coexistence_verifier()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {
                "iso": root / "installer.iso",
                "fixture": root / "windows.qcow2",
                "uefi_code": root / "OVMF_CODE.fd",
                "uefi_vars": root / "windows-vars.fd",
            }
            for name, path in inputs.items():
                path.write_bytes(name.encode())
            output = root / "campaign"
            output.mkdir()
            target = output / "coexistence-target.qcow2"
            variables = output / "uefi-vars.fd"
            evidence = output / "evidence.qcow2"
            target.write_bytes(b"target")
            variables.write_bytes(b"changed-vars")
            evidence.write_bytes(b"evidence")
            (output / "serial.log").write_text("serial evidence")
            case = {
                **{name: str(path) for name, path in inputs.items()},
                **{
                    f"{name}_sha256": runner.sha256_file(path)
                    for name, path in inputs.items()
                },
                "power_cut_marker": "",
            }
            (output / "case.json").write_text(json.dumps(case))
            runner.write_run_result(
                output,
                mode="normal",
                returncode=0,
                started_at="2026-01-01T00:00:00+00:00",
                disk=target,
                vars_copy=variables,
                evidence_disk=evidence,
            )

            result = verifier.verify_campaign(output, "normal")
            self.assertTrue(result["artifacts_valid"])
            self.assertTrue(result["manual_review_required"])

            evidence.write_bytes(b"mutated")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                verifier.verify_campaign(output, "normal")
