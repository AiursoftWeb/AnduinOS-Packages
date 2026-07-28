#!/usr/bin/env python3
"""Create and boot one isolated installer test VM.

Dry-run is the default.  The runner never accepts a host block device and
always creates a fresh qcow2 disk beneath --output.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--iso", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--uefi-code", type=Path)
    parser.add_argument("--uefi-vars", type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually create the qcow2 image and start QEMU",
    )
    return parser.parse_args()


def load_case(case_id):
    matrix_path = Path(__file__).with_name("matrix.json")
    matrix = json.loads(matrix_path.read_text())
    try:
        case = next(item for item in matrix["cases"] if item["id"] == case_id)
    except StopIteration:
        raise SystemExit(f"Unknown matrix case: {case_id}")
    return matrix, case


def require_regular_file(path, description):
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"{description} is not a regular file: {path}")
    return path


def build_command(args, matrix, case, disk, vars_copy):
    architecture = case["architecture"]
    qemu = (
        "qemu-system-x86_64"
        if architecture == "amd64"
        else "qemu-system-aarch64"
    )
    command = [
        qemu,
        "-m",
        str(matrix["memory_mib"]),
        "-smp",
        "4",
        "-no-reboot",
        "-serial",
        f"file:{args.output / 'serial.log'}",
        "-drive",
        f"if=none,id=target,file={disk},format=qcow2",
        "-device",
        "virtio-blk-pci,drive=target",
        "-drive",
        f"if=none,id=install,media=cdrom,readonly=on,file={args.iso.resolve()}",
        "-device",
        "virtio-scsi-pci,id=scsi",
        "-device",
        "scsi-cd,drive=install",
    ]
    if architecture == "amd64":
        command[1:1] = ["-machine", "q35,accel=kvm:tcg", "-cpu", "max"]
    else:
        command[1:1] = [
            "-machine",
            "virt,accel=kvm:tcg",
            "-cpu",
            "max",
        ]
    if case["firmware"] == "uefi":
        if args.uefi_code is None or args.uefi_vars is None:
            raise SystemExit("UEFI cases require --uefi-code and --uefi-vars")
        command += [
            "-drive",
            f"if=pflash,format=raw,readonly=on,file={args.uefi_code.resolve()}",
            "-drive",
            f"if=pflash,format=raw,file={vars_copy}",
        ]
    return command


def main():
    args = parse_args()
    matrix, case = load_case(args.case)
    args.iso = require_regular_file(args.iso, "ISO")
    if case["firmware"] == "uefi":
        args.uefi_code = require_regular_file(args.uefi_code, "UEFI code")
        args.uefi_vars = require_regular_file(args.uefi_vars, "UEFI vars")

    output = args.output.resolve()
    disk = output / "target.qcow2"
    vars_copy = output / "uefi-vars.fd"
    if disk.exists():
        raise SystemExit(f"Refusing to overwrite existing test disk: {disk}")

    command = build_command(args, matrix, case, disk, vars_copy)
    print(shlex.join(command))
    if not args.execute:
        print("Dry run only; pass --execute to create and boot the VM.")
        return 0

    for executable in ("qemu-img", command[0]):
        if shutil.which(executable) is None:
            raise SystemExit(f"Required executable is missing: {executable}")
    output.mkdir(parents=True, exist_ok=True)
    if case["firmware"] == "uefi":
        shutil.copyfile(args.uefi_vars, vars_copy)
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            str(disk),
            f"{matrix['disk_gib']}G",
        ],
        check=True,
    )
    metadata = {**case, "iso": str(args.iso), "command": command}
    (output / "case.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
