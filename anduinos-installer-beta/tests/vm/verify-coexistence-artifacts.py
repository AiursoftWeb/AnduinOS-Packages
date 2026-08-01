#!/usr/bin/env python3
"""Strictly verify retained host-side coexistence campaign artifacts."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESULT_FILES = {
    "normal": "run-result.json",
    "power-cut": "run-result-power-cut.json",
    "recovery": "run-result-recovery.json",
}


def parse_args(arguments=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--result",
        choices=tuple(RESULT_FILES),
        required=True,
    )
    return parser.parse_args(arguments)


def regular_file(path, description):
    if path.is_symlink():
        raise ValueError(f"{description} cannot be a symlink: {path}")
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as error:
        raise ValueError(f"Missing {description}: {path}") from error
    if not stat.S_ISREG(mode):
        raise ValueError(f"{description} is not a regular file: {path}")
    return path


def sha256_file(path):
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path, description):
    regular_file(path, description)
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid {description}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {description} object")
    return value


def require_sha256(value, description):
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"Invalid {description} SHA-256")
    return value


def validate_result(result, expected_mode):
    fields = {
        "schema_version",
        "mode",
        "started_at",
        "finished_at",
        "qemu_returncode",
        "requires_manual_review",
        "test_passed",
        "artifacts",
    }
    if set(result) != fields or result["schema_version"] != 1:
        raise ValueError("Invalid run-result fields or schema")
    modes = {
        "normal": "normal",
        "power-cut": "power-cut",
        "recovery": "power-cut-recovery",
    }
    if result["mode"] != modes[expected_mode]:
        raise ValueError("Run-result mode does not match requested artifact")
    if (
        not isinstance(result["qemu_returncode"], int)
        or isinstance(result["qemu_returncode"], bool)
        or not isinstance(result["started_at"], str)
        or not result["started_at"]
        or not isinstance(result["finished_at"], str)
        or not result["finished_at"]
        or result["requires_manual_review"] is not True
        or result["test_passed"] is not None
    ):
        raise ValueError("Run-result cannot establish an automatic pass")
    artifacts = result["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "target_sha256",
        "uefi_vars_sha256",
        "evidence_sha256",
    }:
        raise ValueError("Invalid run-result artifact fields")
    for name, digest in artifacts.items():
        require_sha256(digest, name)
    return artifacts


def verify_campaign(output, result_kind):
    output = output.resolve()
    if not output.is_dir():
        raise ValueError(f"Campaign directory does not exist: {output}")
    case = load_object(output / "case.json", "case metadata")
    result = load_object(
        output / RESULT_FILES[result_kind],
        "run result",
    )
    input_paths = {
        "iso": "iso_sha256",
        "fixture": "fixture_sha256",
        "uefi_code": "uefi_code_sha256",
        "uefi_vars": "uefi_vars_sha256",
    }
    for path_field, digest_field in input_paths.items():
        path_value = case.get(path_field)
        if not isinstance(path_value, str):
            raise ValueError(f"Case metadata is missing {path_field}")
        path = regular_file(Path(path_value), path_field)
        expected = require_sha256(case.get(digest_field), digest_field)
        if sha256_file(path) != expected:
            raise ValueError(f"Retained input changed: {path_field}")

    artifacts = validate_result(result, result_kind)
    retained = {
        "target_sha256": output / "coexistence-target.qcow2",
        "uefi_vars_sha256": output / "uefi-vars.fd",
        "evidence_sha256": output / "evidence.qcow2",
    }
    for digest_field, path in retained.items():
        regular_file(path, digest_field)
        if sha256_file(path) != artifacts[digest_field]:
            raise ValueError(f"Retained artifact changed: {path.name}")

    serial = (
        output / "serial-recovery.log"
        if result_kind == "recovery"
        else output / "serial.log"
    )
    regular_file(serial, "serial log")
    if result_kind == "power-cut":
        power_cut = load_object(output / "power-cut.json", "power-cut record")
        if power_cut != {
            "marker": case.get("power_cut_marker"),
            "triggered": True,
        }:
            raise ValueError("Power-cut record does not match the case")
    return {
        "artifacts_valid": True,
        "result": result_kind,
        "manual_review_required": True,
    }


def main(arguments=None):
    args = parse_args(arguments)
    try:
        print(json.dumps(verify_campaign(args.output, args.result)))
        return 0
    except Exception as error:
        print(json.dumps({"artifacts_valid": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
