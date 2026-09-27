"""Fixed privileged entry point for Rescue Center operations."""

from __future__ import annotations

import json
import os
import sys

from .storage import inspect_target, probe_inventory
from .operations import reset_password
from .files import export_file, list_files
from .snapshots import create_snapshot, list_snapshots, restore_snapshot
from .boot import diagnose_boot, repair_boot
from .offline_env import emergency_shell


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0:
        print("The rescue helper must run as root", file=sys.stderr)
        return 77
    try:
        if len(arguments) == 3 and arguments[0] == "shell":
            return emergency_shell(arguments[1], arguments[2])
        if arguments == ["probe"]:
            payload = probe_inventory().to_dict()
        elif len(arguments) == 3 and arguments[0] == "inspect":
            payload = inspect_target(arguments[1], arguments[2])
        elif len(arguments) == 4 and arguments[0] == "reset-password":
            password = sys.stdin.read(4097)
            if len(password) > 4096:
                raise ValueError("The new password is too long")
            reset_password(arguments[1], arguments[2], arguments[3], password)
            payload = {"schema": 1, "changed": True}
        elif len(arguments) == 4 and arguments[0] == "list-files":
            payload = list_files(arguments[1], arguments[2], arguments[3])
        elif len(arguments) == 5 and arguments[0] == "export":
            uid_text = os.environ.get("PKEXEC_UID", "")
            if not uid_text.isdigit():
                raise RuntimeError("Could not identify the desktop user")
            exported = export_file(
                arguments[1], arguments[2], arguments[3], arguments[4],
                caller_uid=int(uid_text),
            )
            payload = {"schema": 1, "exported": exported}
        elif len(arguments) == 3 and arguments[0] == "list-snapshots":
            payload = list_snapshots(arguments[1], arguments[2])
        elif len(arguments) == 4 and arguments[0] == "create-snapshot":
            payload = create_snapshot(arguments[1], arguments[2], arguments[3])
        elif len(arguments) == 5 and arguments[0] == "restore-snapshot":
            if arguments[4] not in {"true", "false"}:
                raise ValueError("Invalid protection choice")
            payload = restore_snapshot(
                arguments[1], arguments[2], arguments[3], arguments[4] == "true"
            )
        elif len(arguments) == 3 and arguments[0] == "diagnose-boot":
            payload = diagnose_boot(arguments[1], arguments[2])
        elif len(arguments) == 4 and arguments[0] == "repair-boot":
            payload = repair_boot(arguments[1], arguments[2], arguments[3])
        elif len(arguments) == 4 and arguments[0] == "repair-boot-stream":
            def progress(message: str) -> None:
                print("RESCUE_PROGRESS\t" + json.dumps({"message": message}),
                      file=sys.stderr, flush=True)

            payload = repair_boot(arguments[1], arguments[2], arguments[3], progress=progress)
        else:
            print(
                "Usage: anduinos-rescue-center-helper "
                "{probe|inspect DEVICE IDENTITY|reset-password DEVICE IDENTITY USER|"
                "list-files DEVICE IDENTITY PATH|export DEVICE IDENTITY PATH DESTINATION|"
                "list-snapshots DEVICE IDENTITY|create-snapshot DEVICE IDENTITY TITLE|"
                "restore-snapshot DEVICE IDENTITY ID {true|false}|"
                "diagnose-boot DEVICE IDENTITY|repair-boot DEVICE IDENTITY EFI_IDENTITY|"
                "repair-boot-stream DEVICE IDENTITY EFI_IDENTITY|"
                "shell DEVICE IDENTITY}",
                file=sys.stderr,
            )
            return 64
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0
