#!/usr/bin/python3
"""Run disposable-QEMU power-cut qualification against an armed fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid


CONFIRMATION = "DESTROY_TM5_QEMU_OVERLAYS"
APPLY_CHECKPOINTS = (
    "apply-started",
    "writable-target-created",
    "current-root-protected",
    "target-root-activated",
    "booted-unconfirmed-recorded",
)
REVERT_CHECKPOINTS = (
    "revert-started",
    "restored-root-moved-aside",
    "fallback-root-activated",
    "discarded-root-deleted",
    "reverted-recorded",
)
ALL_CHECKPOINTS = APPLY_CHECKPOINTS + REVERT_CHECKPOINTS
CHECKPOINT_PREFIX = "TIMEBACK-CHECKPOINT "
FALLBACK_RESULT = "TM-5-RESULT fallback passed"


class QualificationError(RuntimeError):
    pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Power-cut an armed AnduinOS Timeback QEMU fixture",
    )
    result.add_argument("--fixture", required=True, type=Path)
    result.add_argument("--output-dir", required=True, type=Path)
    result.add_argument("--confirm", required=True)
    result.add_argument("--qemu-system", default="qemu-system-x86_64")
    result.add_argument("--qemu-img", default="qemu-img")
    result.add_argument("--machine", default="q35,accel=tcg")
    result.add_argument("--cpu", default="max")
    result.add_argument("--memory-mib", type=int, default=4096)
    result.add_argument("--cpus", type=int, default=2)
    result.add_argument("--timeout", type=int, default=300)
    result.add_argument("--uefi-code", type=Path)
    result.add_argument("--uefi-vars", type=Path)
    result.add_argument(
        "--checkpoint",
        action="append",
        choices=ALL_CHECKPOINTS,
        dest="checkpoints",
        help="run only this checkpoint; may be repeated",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and create no overlay or VM",
    )
    return result


def fail(message: str) -> None:
    raise QualificationError(message)


def regular_real_file(path: Path, name: str) -> Path:
    absolute = path.expanduser().absolute()
    if absolute.is_symlink() or not absolute.is_file():
        fail(f"{name} must be a regular non-symlink file: {absolute}")
    return absolute.resolve(strict=True)


def real_directory(path: Path, name: str) -> Path:
    absolute = path.expanduser().absolute()
    if absolute.is_symlink() or not absolute.is_dir():
        fail(f"{name} must be an existing non-symlink directory: {absolute}")
    return absolute.resolve(strict=True)


def executable(value: str) -> str:
    resolved = shutil.which(value)
    if not resolved:
        fail(f"required executable is missing: {value}")
    return resolved


def qemu_keyval_path(path: Path, name: str) -> None:
    if "," in os.fspath(path):
        fail(f"{name} cannot contain a comma because QEMU key-value syntax is used")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def run_checked(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def validate_fixture(qemu_img: str, fixture: Path) -> None:
    result = run_checked(
        [qemu_img, "info", "--output=json", str(fixture)],
        capture=True,
    )
    info = json.loads(result.stdout)
    if info.get("format") != "qcow2":
        fail("the prepared fixture must use qcow2 format")
    run_checked([qemu_img, "check", "-q", str(fixture)])


def unique_run_directory(output: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run = output / f"tm5-powercut-{stamp}-{uuid.uuid4().hex[:8]}"
    run.mkdir(mode=0o700)
    return run


def qemu_command(
    args: argparse.Namespace,
    overlay: Path,
    serial_socket: Path,
    qmp_socket: Path,
    firmware_vars: Path | None,
) -> list[str]:
    command = [
        args.qemu_system,
        "-name",
        "AnduinOS-TM5-qualification",
        "-machine",
        args.machine,
        "-cpu",
        args.cpu,
        "-m",
        str(args.memory_mib),
        "-smp",
        str(args.cpus),
        "-display",
        "none",
        "-monitor",
        "none",
        "-no-reboot",
        "-nic",
        "none",
        "-drive",
        f"file={overlay},if=virtio,format=qcow2,cache=writeback",
        "-chardev",
        f"socket,id=tm5serial,path={serial_socket},server=on,wait=on",
        "-serial",
        "chardev:tm5serial",
        "-qmp",
        f"unix:{qmp_socket},server=on,wait=off",
        "-fw_cfg",
        "name=opt/anduinos/timeback-expected,string=fallback",
    ]
    if args.uefi_code is not None:
        assert firmware_vars is not None
        command.extend(
            [
                "-drive",
                f"if=pflash,format=raw,readonly=on,file={args.uefi_code}",
                "-drive",
                f"if=pflash,format=raw,file={firmware_vars}",
            ]
        )
    return command


def connect_unix(path: Path, process: subprocess.Popen[bytes], timeout: float) -> socket.socket:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            fail(f"QEMU exited before creating {path.name}: {process.returncode}")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect(str(path))
            return client
        except OSError as error:
            last_error = error
            client.close()
            time.sleep(0.05)
    fail(f"QEMU did not create {path.name}: {last_error}")


def start_vm(
    args: argparse.Namespace,
    scenario: Path,
    overlay: Path,
    firmware_vars: Path | None,
    boot_number: int,
) -> tuple[subprocess.Popen[bytes], socket.socket, Path, Path]:
    serial_socket = scenario / f"serial-{boot_number}.sock"
    qmp_socket = scenario / f"qmp-{boot_number}.sock"
    if len(os.fsencode(serial_socket)) >= 100 or len(os.fsencode(qmp_socket)) >= 100:
        fail("output path is too long for QEMU Unix sockets")
    host_log = (scenario / f"qemu-{boot_number}.log").open("wb")
    try:
        process = subprocess.Popen(
            qemu_command(args, overlay, serial_socket, qmp_socket, firmware_vars),
            stdin=subprocess.DEVNULL,
            stdout=host_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        host_log.close()
    try:
        serial = connect_unix(serial_socket, process, 15)
    except Exception:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)
        raise
    return process, serial, serial_socket, qmp_socket


def wait_for_serial(
    process: subprocess.Popen[bytes],
    serial: socket.socket,
    output: Path,
    needle: str,
    timeout: int,
) -> None:
    deadline = time.monotonic() + timeout
    wanted = needle.encode()
    recent = bytearray()
    serial.settimeout(0.25)
    with output.open("wb") as log:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                fail(f"QEMU exited before serial event {needle!r}: {process.returncode}")
            try:
                block = serial.recv(4096)
            except socket.timeout:
                continue
            if not block:
                time.sleep(0.05)
                continue
            log.write(block)
            log.flush()
            recent.extend(block)
            if wanted in recent:
                return
            if len(recent) > 64 * 1024:
                del recent[:-32 * 1024]
    fail(f"timed out waiting for serial event {needle!r}")


def kill_power(process: subprocess.Popen[bytes], serial: socket.socket) -> None:
    serial.close()
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=30)


def qmp_powerdown(qmp_socket: Path) -> None:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    try:
        client.connect(str(qmp_socket))
        client.recv(4096)
        client.sendall(b'{"execute":"qmp_capabilities"}\r\n')
        client.recv(4096)
        client.sendall(b'{"execute":"system_powerdown"}\r\n')
    finally:
        client.close()


def stop_after_result(
    process: subprocess.Popen[bytes],
    serial: socket.socket,
    qmp_socket: Path,
) -> None:
    serial.close()
    try:
        qmp_powerdown(qmp_socket)
        process.wait(timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)


def create_overlay(qemu_img: str, fixture: Path, overlay: Path) -> None:
    run_checked(
        [
            qemu_img,
            "create",
            "-q",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(fixture),
            str(overlay),
        ]
    )


def cut_at(
    args: argparse.Namespace,
    scenario: Path,
    overlay: Path,
    firmware_vars: Path | None,
    boot_number: int,
    checkpoint: str,
) -> None:
    process, serial, _serial_socket, _qmp_socket = start_vm(
        args, scenario, overlay, firmware_vars, boot_number
    )
    try:
        wait_for_serial(
            process,
            serial,
            scenario / f"serial-{boot_number}.log",
            CHECKPOINT_PREFIX + checkpoint,
            args.timeout,
        )
        kill_power(process, serial)
    finally:
        if process.poll() is None:
            kill_power(process, serial)


def verify_fallback(
    args: argparse.Namespace,
    scenario: Path,
    overlay: Path,
    firmware_vars: Path | None,
    boot_number: int,
) -> None:
    process, serial, _serial_socket, qmp_socket = start_vm(
        args, scenario, overlay, firmware_vars, boot_number
    )
    try:
        wait_for_serial(
            process,
            serial,
            scenario / f"serial-{boot_number}.log",
            FALLBACK_RESULT,
            args.timeout,
        )
        stop_after_result(process, serial, qmp_socket)
    finally:
        if process.poll() is None:
            kill_power(process, serial)


def run_scenario(
    args: argparse.Namespace,
    run: Path,
    fixture: Path,
    checkpoint: str,
    index: int,
) -> dict[str, object]:
    scenario = run / f"{index:02d}-{checkpoint}"
    scenario.mkdir(mode=0o700)
    overlay = scenario / "disk.qcow2"
    create_overlay(args.qemu_img, fixture, overlay)
    firmware_vars = None
    if args.uefi_vars is not None:
        firmware_vars = scenario / "uefi-vars.fd"
        shutil.copyfile(args.uefi_vars, firmware_vars)

    started = time.monotonic()
    if checkpoint in APPLY_CHECKPOINTS:
        cut_at(args, scenario, overlay, firmware_vars, 1, checkpoint)
        verify_fallback(args, scenario, overlay, firmware_vars, 2)
        boots = 2
    else:
        cut_at(args, scenario, overlay, firmware_vars, 1, "target-root-activated")
        cut_at(args, scenario, overlay, firmware_vars, 2, checkpoint)
        verify_fallback(args, scenario, overlay, firmware_vars, 3)
        boots = 3
    result = {
        "checkpoint": checkpoint,
        "status": "passed",
        "boots": boots,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (scenario / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.confirm != CONFIRMATION:
        fail(f"--confirm must be exactly {CONFIRMATION}")
    if args.memory_mib < 1024 or args.memory_mib > 262144:
        fail("--memory-mib must be between 1024 and 262144")
    if args.cpus < 1 or args.cpus > 256:
        fail("--cpus must be between 1 and 256")
    if args.timeout < 30 or args.timeout > 3600:
        fail("--timeout must be between 30 and 3600 seconds")

    fixture = regular_real_file(args.fixture, "fixture")
    output = real_directory(args.output_dir, "output directory")
    args.qemu_system = executable(args.qemu_system)
    args.qemu_img = executable(args.qemu_img)
    if (args.uefi_code is None) != (args.uefi_vars is None):
        fail("--uefi-code and --uefi-vars must be supplied together")
    if args.uefi_code is not None:
        args.uefi_code = regular_real_file(args.uefi_code, "UEFI code image")
        args.uefi_vars = regular_real_file(args.uefi_vars, "UEFI vars fixture")

    qemu_keyval_path(output, "output directory")
    if args.uefi_code is not None:
        qemu_keyval_path(args.uefi_code, "UEFI code image")
        qemu_keyval_path(args.uefi_vars, "UEFI vars fixture")

    validate_fixture(args.qemu_img, fixture)
    checkpoints = tuple(dict.fromkeys(args.checkpoints or ALL_CHECKPOINTS))
    print(f"Fixture: {fixture}")
    print(f"Scenarios: {', '.join(checkpoints)}")
    if args.dry_run:
        print("Dry run passed; no overlay or VM was created")
        return 0

    fixture_digest = sha256(fixture)
    vars_digest = sha256(args.uefi_vars) if args.uefi_vars is not None else None
    run = unique_run_directory(output)
    summary: dict[str, object] = {
        "schema_version": 1,
        "fixture": str(fixture),
        "fixture_sha256": fixture_digest,
        "results": [],
    }
    try:
        for index, checkpoint in enumerate(checkpoints, 1):
            print(f"[{index}/{len(checkpoints)}] power cut at {checkpoint}", flush=True)
            result = run_scenario(args, run, fixture, checkpoint, index)
            summary["results"].append(result)
    finally:
        unchanged = sha256(fixture) == fixture_digest
        vars_unchanged = (
            args.uefi_vars is None or sha256(args.uefi_vars) == vars_digest
        )
        summary["fixture_unchanged"] = unchanged
        summary["uefi_vars_fixture_unchanged"] = vars_unchanged
        (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        if not unchanged or not vars_unchanged:
            fail("a supposedly read-only fixture changed during qualification")
    print(f"TM-5 QEMU power-cut qualification passed: {run}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (QualificationError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"TM-5 QEMU qualification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
