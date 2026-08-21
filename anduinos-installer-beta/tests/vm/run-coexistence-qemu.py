#!/usr/bin/env python3
"""Clone and boot one disposable Windows-shaped coexistence fixture."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


POWER_CUT_RE = re.compile(
    r"^(guided-[a-z0-9]+(?:-[a-z0-9]+)*):(before|after)$"
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--iso", required=True, type=Path)
    parser.add_argument("--iso-sha256", required=True)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--fixture-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--uefi-code", required=True, type=Path)
    parser.add_argument("--uefi-code-sha256", required=True)
    parser.add_argument("--uefi-vars", required=True, type=Path)
    parser.add_argument("--uefi-vars-sha256", required=True)
    parser.add_argument(
        "--power-cut-at",
        default="",
        help="kill QEMU at BOUNDARY:before or BOUNDARY:after",
    )
    parser.add_argument(
        "--power-cut-timeout",
        type=int,
        default=7200,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="clone the fixture and start QEMU",
    )
    parser.add_argument(
        "--resume-after-power-cut",
        action="store_true",
        help="boot an existing campaign only after a recorded power cut",
    )
    return parser.parse_args()


def power_cut_marker(value):
    match = POWER_CUT_RE.fullmatch(value)
    if match is None:
        raise SystemExit(
            "Power-cut target must be guided-BOUNDARY:before or :after"
        )
    return f"[anduinos-boundary:{match.group(1)}:{match.group(2)}]"


def load_case(case_id):
    matrix_path = Path(__file__).with_name("coexistence-matrix.json")
    matrix = json.loads(matrix_path.read_text())
    try:
        case = next(item for item in matrix["cases"] if item["id"] == case_id)
    except StopIteration:
        raise SystemExit(f"Unknown coexistence matrix case: {case_id}")
    return matrix, case


def require_regular_file(path, description):
    path = path.resolve()
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as error:
        raise SystemExit(f"{description} does not exist: {path}") from error
    if not stat.S_ISREG(mode):
        raise SystemExit(f"{description} is not a regular file: {path}")
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path, expected_sha256, description):
    expected = expected_sha256.casefold()
    if len(expected) != 64 or any(
        item not in "0123456789abcdef" for item in expected
    ):
        raise SystemExit(
            f"{description} SHA-256 must contain exactly 64 hex digits"
        )
    actual = sha256_file(path)
    if actual != expected:
        raise SystemExit(
            f"{description} SHA-256 mismatch: expected {expected}, "
            f"found {actual}"
        )
    return actual


def verify_fixture(path, expected_sha256):
    return verify_file(path, expected_sha256, "Fixture")


def build_command(
    args,
    matrix,
    case,
    disk,
    vars_copy,
    evidence_disk,
    *,
    serial_log=None,
):
    serial_log = serial_log or args.output / "serial.log"
    return [
        "qemu-system-x86_64",
        "-machine",
        "q35,accel=kvm:tcg",
        "-cpu",
        "max",
        "-m",
        str(matrix["memory_mib"]),
        "-smp",
        "4",
        "-no-reboot",
        "-serial",
        f"file:{serial_log}",
        "-drive",
        f"if=none,id=target,file={disk},format=qcow2",
        "-device",
        (
            "virtio-blk-pci,drive=target,"
            "serial=ANDUINOS-COEXISTENCE-TARGET"
        ),
        "-drive",
        f"if=none,id=evidence,file={evidence_disk},format=qcow2",
        "-device",
        "virtio-blk-pci,drive=evidence,serial=ANDUINOS-EVIDENCE",
        "-drive",
        (
            "if=none,id=install,media=cdrom,readonly=on,file="
            f"{args.iso.resolve()}"
        ),
        "-device",
        "virtio-scsi-pci,id=scsi",
        "-device",
        "scsi-cd,drive=install",
        "-drive",
        f"if=pflash,format=raw,readonly=on,file={args.uefi_code.resolve()}",
        "-drive",
        f"if=pflash,format=raw,file={vars_copy}",
    ]


def run_until_power_cut(command, serial_log, marker, timeout):
    """SIGKILL QEMU only after its serial log contains the exact marker."""

    if timeout <= 0:
        raise SystemExit("Power-cut timeout must be positive")
    process = subprocess.Popen(command)
    deadline = time.monotonic() + timeout
    offset = 0
    pending = ""
    while time.monotonic() < deadline:
        if serial_log.is_file():
            with serial_log.open(
                "r", encoding="utf-8", errors="replace"
            ) as stream:
                stream.seek(offset)
                output = stream.read()
                offset = stream.tell()
            pending += output
            if marker in pending:
                process.kill()
                return process.wait()
            pending = pending[-len(marker) :]
        returncode = process.poll()
        if returncode is not None:
            raise SystemExit(
                "QEMU exited before the requested power-cut boundary "
                f"(status {returncode})"
            )
        time.sleep(0.2)
    process.kill()
    process.wait()
    raise SystemExit(
        f"Timed out waiting for power-cut boundary: {marker}"
    )


def validate_resume_campaign(
    output,
    args,
    case,
    fixture_sha256,
    iso_sha256,
    uefi_code_sha256,
    uefi_vars_sha256,
    disk,
    vars_copy,
    evidence_disk,
):
    if not output.is_dir():
        raise SystemExit(f"Campaign directory does not exist: {output}")
    for path, description in (
        (disk, "coexistence target"),
        (vars_copy, "campaign UEFI vars"),
        (evidence_disk, "campaign evidence disk"),
        (output / "case.json", "campaign metadata"),
        (output / "power-cut.json", "power-cut record"),
    ):
        require_regular_file(path, description)
    try:
        metadata = json.loads((output / "case.json").read_text())
        power_cut = json.loads((output / "power-cut.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Campaign metadata is invalid: {error}") from error
    expected = {
        "case": case["id"],
        "iso": str(args.iso),
        "fixture": str(args.fixture),
        "uefi_code": str(args.uefi_code),
        "uefi_vars": str(args.uefi_vars),
        "fixture_sha256": fixture_sha256,
        "iso_sha256": iso_sha256,
        "uefi_code_sha256": uefi_code_sha256,
        "uefi_vars_sha256": uefi_vars_sha256,
        "evidence_disk": str(evidence_disk),
    }
    actual = {
        "case": metadata.get("id"),
        "iso": metadata.get("iso"),
        "fixture": metadata.get("fixture"),
        "uefi_code": metadata.get("uefi_code"),
        "uefi_vars": metadata.get("uefi_vars"),
        "fixture_sha256": metadata.get("fixture_sha256"),
        "iso_sha256": metadata.get("iso_sha256"),
        "uefi_code_sha256": metadata.get("uefi_code_sha256"),
        "uefi_vars_sha256": metadata.get("uefi_vars_sha256"),
        "evidence_disk": metadata.get("evidence_disk"),
    }
    if actual != expected:
        raise SystemExit("Campaign metadata does not match resume arguments")
    if (
        metadata.get("executor_policy") != "guided-destructive-test"
        or not metadata.get("power_cut_marker")
        or power_cut
        != {"marker": metadata["power_cut_marker"], "triggered": True}
    ):
        raise SystemExit("Campaign has no valid recorded power cut")


def tool_version(command):
    result = subprocess.run(
        (command, "--version"),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else f"{command} exited {result.returncode}"


def runtime_metadata(qemu):
    return {
        "host_system": platform.system(),
        "host_release": platform.release(),
        "host_machine": platform.machine(),
        "qemu": tool_version(qemu),
        "qemu_img": tool_version("qemu-img"),
    }


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_run_result(
    output,
    *,
    mode,
    returncode,
    started_at,
    disk,
    vars_copy,
    evidence_disk,
):
    result = {
        "schema_version": 1,
        "mode": mode,
        "started_at": started_at,
        "finished_at": utc_now(),
        "qemu_returncode": returncode,
        "requires_manual_review": True,
        "test_passed": None,
        "artifacts": {
            "target_sha256": sha256_file(disk),
            "uefi_vars_sha256": sha256_file(vars_copy),
            "evidence_sha256": sha256_file(evidence_disk),
        },
    }
    filename = {
        "normal": "run-result.json",
        "power-cut": "run-result-power-cut.json",
        "power-cut-recovery": "run-result-recovery.json",
    }[mode]
    (output / filename).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )


def main():
    args = parse_args()
    matrix, case = load_case(args.case)
    args.iso = require_regular_file(args.iso, "ISO")
    args.fixture = require_regular_file(args.fixture, "Coexistence fixture")
    args.uefi_code = require_regular_file(args.uefi_code, "UEFI code")
    args.uefi_vars = require_regular_file(args.uefi_vars, "UEFI vars")
    iso_sha256 = verify_file(args.iso, args.iso_sha256, "ISO")
    fixture_sha256 = verify_fixture(args.fixture, args.fixture_sha256)
    uefi_code_sha256 = verify_file(
        args.uefi_code, args.uefi_code_sha256, "UEFI code"
    )
    uefi_vars_sha256 = verify_file(
        args.uefi_vars, args.uefi_vars_sha256, "UEFI vars"
    )
    marker = (
        power_cut_marker(args.power_cut_at)
        if args.power_cut_at
        else ""
    )
    if args.resume_after_power_cut and marker:
        raise SystemExit(
            "A resumed campaign cannot request another power-cut marker"
        )

    output = args.output.resolve()
    disk = output / "coexistence-target.qcow2"
    evidence_disk = output / "evidence.qcow2"
    vars_copy = output / "uefi-vars.fd"
    if output.exists() and not args.resume_after_power_cut:
        raise SystemExit(
            f"Refusing to use an existing VM campaign directory: {output}"
        )
    if args.resume_after_power_cut:
        validate_resume_campaign(
            output,
            args,
            case,
            fixture_sha256,
            iso_sha256,
            uefi_code_sha256,
            uefi_vars_sha256,
            disk,
            vars_copy,
            evidence_disk,
        )
    command = build_command(
        args,
        matrix,
        case,
        disk,
        vars_copy,
        evidence_disk,
        serial_log=(
            output / "serial-recovery.log"
            if args.resume_after_power_cut
            else None
        ),
    )
    print(shlex.join(command))
    if not args.execute:
        action = "resume" if args.resume_after_power_cut else "clone and boot"
        print(f"Dry run only; pass --execute to {action} the fixture.")
        return 0

    executables = (
        (command[0],)
        if args.resume_after_power_cut
        else ("qemu-img", command[0])
    )
    for executable in executables:
        if shutil.which(executable) is None:
            raise SystemExit(f"Required executable is missing: {executable}")
    started_at = utc_now()
    if args.resume_after_power_cut:
        returncode = subprocess.run(command, check=False).returncode
        write_run_result(
            output,
            mode="power-cut-recovery",
            returncode=returncode,
            started_at=started_at,
            disk=disk,
            vars_copy=vars_copy,
            evidence_disk=evidence_disk,
        )
        return returncode
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.uefi_vars, vars_copy)
    subprocess.run(
        [
            "qemu-img",
            "convert",
            "-f",
            "qcow2",
            "-O",
            "qcow2",
            str(args.fixture),
            str(disk),
        ],
        check=True,
    )
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            str(evidence_disk),
            f"{matrix['evidence_disk_mib']}M",
        ],
        check=True,
    )
    metadata = {
        **case,
        "iso": str(args.iso),
        "fixture": str(args.fixture),
        "uefi_code": str(args.uefi_code),
        "uefi_vars": str(args.uefi_vars),
        "fixture_sha256": fixture_sha256,
        "iso_sha256": iso_sha256,
        "uefi_code_sha256": uefi_code_sha256,
        "uefi_vars_sha256": uefi_vars_sha256,
        "command": command,
        "runtime": runtime_metadata(command[0]),
        "executor_policy": "guided-destructive-test",
        "power_cut_marker": marker,
        "evidence_disk": str(evidence_disk),
    }
    (output / "case.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    if marker:
        returncode = run_until_power_cut(
            command,
            output / "serial.log",
            marker,
            args.power_cut_timeout,
        )
        (output / "power-cut.json").write_text(
            json.dumps({"marker": marker, "triggered": True}, indent=2)
            + "\n"
        )
        write_run_result(
            output,
            mode="power-cut",
            returncode=returncode,
            started_at=started_at,
            disk=disk,
            vars_copy=vars_copy,
            evidence_disk=evidence_disk,
        )
        return 0
    returncode = subprocess.run(command, check=False).returncode
    write_run_result(
        output,
        mode="normal",
        returncode=returncode,
        started_at=started_at,
        disk=disk,
        vars_copy=vars_copy,
        evidence_disk=evidence_disk,
    )
    return returncode


if __name__ == "__main__":
    sys.exit(main())
