"""Internal read-only planner for disposable guided-coexistence VMs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from installer_core.destructive_test import (
    GUIDED_TEST_FLAG,
    execution_policy,
    require_disposable_test_environment,
)
from installer_core.guided_test_plan import build_guided_vm_test_plan
from installer_core.model import Filesystem
from installer_core.probe import probe_platform
from installer_core.storage_inventory import probe_storage_inventory
from installer_core.storage_ui import (
    GuidedStorageSelection,
    build_storage_workflow,
)


def parse_args(arguments: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument(GUIDED_TEST_FLAG, action="store_true", required=True)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("inspect")
    build = subparsers.add_parser("build")
    build.add_argument("--disk-stable-id", required=True)
    build.add_argument("--extent-id", required=True)
    build.add_argument(
        "--filesystem",
        choices=("btrfs", "ext4"),
        required=True,
    )
    esp = build.add_mutually_exclusive_group(required=True)
    esp.add_argument("--esp-partuuid")
    esp.add_argument("--new-esp", action="store_true")
    build.add_argument("--output", required=True, type=Path)
    return parser.parse_args(arguments)


def inspect_workflow(workflow) -> dict[str, object]:
    return {
        "disks": [
            {
                "path": choice.disk.identity.path,
                "stable_id": choice.disk.identity.stable_id,
                "size_bytes": choice.disk.identity.expected_size_bytes,
                "model": choice.disk.identity.model,
                "guided_available": choice.guided_available,
                "free_extents": [
                    {
                        "extent_id": item.extent.extent_id,
                        "start_bytes": item.extent.start_bytes,
                        "size_bytes": item.extent.size_bytes,
                        "requires_reused_esp": item.requires_reused_esp,
                    }
                    for item in choice.coexistence.free_space_candidates
                ],
                "esp_candidates": [
                    {
                        "partuuid": item.identity.partuuid,
                        "path": item.identity.path,
                        "size_bytes": item.identity.size_bytes,
                    }
                    for item in choice.coexistence.esp_candidates
                ],
                "notices": [
                    {"code": item.code.value, "message": item.message}
                    for item in choice.coexistence.notices
                ],
            }
            for choice in workflow.disks
        ]
    }


def build_plan(args, workflow):
    choice = workflow.disk(args.disk_stable_id)
    require_disposable_test_environment(
        choice.disk.identity.path,
        environment=dict(os.environ),
    )
    selection = GuidedStorageSelection(
        disk_stable_id=choice.disk.identity.stable_id,
        disk_size_bytes=choice.disk.identity.expected_size_bytes,
        free_extent_id=args.extent_id,
        reused_esp_partuuid=(args.esp_partuuid or ""),
        filesystem=Filesystem(args.filesystem),
    )
    return build_guided_vm_test_plan(workflow, selection)


def require_safe_output(path: Path) -> None:
    resolved = path.resolve(strict=False)
    forbidden = (Path("/dev"), Path("/proc"), Path("/sys"))
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise RuntimeError("Plan output cannot target a device or kernel path")


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        execution_policy(
            [GUIDED_TEST_FLAG],
            dict(os.environ),
        )
        inventory = probe_storage_inventory()
        workflow = build_storage_workflow(inventory, probe_platform())
        vda = next(
            (
                choice
                for choice in workflow.disks
                if choice.disk.identity.path == "/dev/vda"
            ),
            None,
        )
        if vda is None:
            raise RuntimeError("Disposable VM target /dev/vda was not found")
        require_disposable_test_environment(
            vda.disk.identity.path,
            environment=dict(os.environ),
        )
        if args.action == "inspect":
            print(json.dumps(inspect_workflow(workflow), indent=2))
            return 0
        plan = build_plan(args, workflow)
        require_safe_output(args.output)
        if args.output.exists():
            raise RuntimeError(f"Refusing to overwrite plan: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(plan.to_dict(), stream, indent=2, sort_keys=True)
            stream.write("\n")
        args.output.chmod(0o600)
        print(json.dumps({"succeeded": True, "plan": str(args.output)}))
        return 0
    except Exception as error:
        print(json.dumps({"succeeded": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
