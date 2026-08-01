"""Root-only before/after evidence CLI for disposable coexistence VMs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from installer_core.boot_commands import guided_loader_path
from installer_core.command import CommandRunner
from installer_core.destructive_test import (
    GUIDED_TEST_FLAG,
    require_disposable_guided_vm,
)
from installer_core.esp import (
    EspReuseInspection,
    inspect_esp_for_reuse,
    inspect_nvram,
    verify_nvram_entry,
)
from installer_core.guided_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    GuidedVmEvidence,
    capture_nvram_evidence,
    capture_partition_digests,
    plan_sha256,
    validate_evidence,
    verify_guided_evidence_topology,
    verify_nvram_evidence,
)
from installer_core.model import InstallPlan
from installer_core.storage_inventory import probe_storage_inventory
from installer_core.storage_planning import (
    build_guided_coexistence_execution_plan,
    resolve_guided_esp_partition,
)
from installer_core.storage_preservation import (
    capture_guided_preservation_snapshot,
)
from installer_core.validation import (
    ExecutionPolicy,
    validate_plan_for_execution,
)


def parse_args(arguments: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("capture", "verify"))
    parser.add_argument(GUIDED_TEST_FLAG, action="store_true", required=True)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    return parser.parse_args(arguments)


def read_plan(path: Path) -> InstallPlan:
    if not path.is_file():
        raise ValueError(f"Plan is not a regular file: {path}")
    return InstallPlan.from_dict(json.loads(path.read_text()))


def require_safe_output(path: Path) -> None:
    resolved = path.resolve(strict=False)
    forbidden = (Path("/dev"), Path("/proc"), Path("/sys"))
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise RuntimeError("Evidence output cannot target a device or kernel path")


def capture(plan: InstallPlan, evidence_path: Path, runner: CommandRunner) -> None:
    require_safe_output(evidence_path)
    if evidence_path.exists():
        raise RuntimeError(f"Refusing to overwrite evidence: {evidence_path}")
    inventory = probe_storage_inventory()
    esp, reuses_esp = resolve_guided_esp_partition(plan, inventory)
    esp_inspection = (
        inspect_esp_for_reuse(esp, runner) if reuses_esp else None
    )
    nvram_inspection = inspect_nvram(runner)
    execution = build_guided_coexistence_execution_plan(
        plan,
        inventory,
        esp_inspection=esp_inspection,
        nvram_inspection=nvram_inspection,
    )
    preservation = capture_guided_preservation_snapshot(
        plan, inventory, execution.write_set
    )
    nvram_output = runner.run(
        ("efibootmgr", "--verbose"), timeout=30
    ).stdout
    verify_windows_fixture_boot_entry(plan, inventory, nvram_output)
    evidence = GuidedVmEvidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        plan_sha256=plan_sha256(plan),
        preservation=preservation,
        partition_digests=capture_partition_digests(
            preservation,
            inventory,
            reused_esp_partuuid=(esp.identity.partuuid if reuses_esp else ""),
        ),
        reused_esp_partuuid=(esp.identity.partuuid if reuses_esp else ""),
        esp_entries=(
            esp_inspection.preserved_entries
            if isinstance(esp_inspection, EspReuseInspection)
            else ()
        ),
        nvram=capture_nvram_evidence(nvram_output),
    )
    validate_evidence(evidence)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with evidence_path.open("x", encoding="utf-8") as stream:
        json.dump(evidence.to_dict(), stream, indent=2, sort_keys=True)
        stream.write("\n")
    evidence_path.chmod(0o600)


def verify_windows_fixture_boot_entry(plan, inventory, output: str) -> None:
    disk = inventory.disk(plan.storage.disk.stable_id)
    candidates = tuple(
        item for item in disk.partitions if item.is_efi_filesystem_candidate
    )
    for partition in candidates:
        try:
            verify_nvram_entry(
                output,
                label="Windows Boot Manager",
                partuuid=partition.identity.partuuid,
                loader=r"\EFI\Microsoft\Boot\bootmgfw.efi",
            )
            return
        except RuntimeError:
            continue
    raise RuntimeError(
        "Paired fixture VARS has no Windows Boot Manager entry for the "
        "target disk ESP"
    )


def verify(plan: InstallPlan, evidence_path: Path, runner: CommandRunner) -> None:
    if not evidence_path.is_file():
        raise RuntimeError(f"Evidence is not a regular file: {evidence_path}")
    evidence = GuidedVmEvidence.from_dict(
        json.loads(evidence_path.read_text())
    )
    inventory = probe_storage_inventory()
    verify_guided_evidence_topology(plan, evidence, inventory)
    disk = inventory.disk(evidence.preservation.disk_stable_id)
    esp = _post_install_esp(plan, evidence, disk.partitions)
    inspection = inspect_esp_for_reuse(esp, runner)
    if not inspection.healthy:
        raise RuntimeError("ESP is unhealthy after installation")
    if (
        evidence.reused_esp_partuuid
        and inspection.preserved_entries != evidence.esp_entries
    ):
        raise RuntimeError(
            "Shared EFI System Partition changed outside EFI/AnduinOS"
        )
    expected_loader = guided_loader_path(plan).replace("\\", "/").lstrip("/")
    vendor_files = {
        item.relative_path.casefold(): item
        for item in inspection.vendor_entries
        if item.kind == "file"
    }
    loader = vendor_files.get(expected_loader.casefold())
    if loader is None or loader.size_bytes <= 0:
        raise RuntimeError("AnduinOS vendor loader is missing from the ESP")
    nvram_output = runner.run(
        ("efibootmgr", "--verbose"), timeout=30
    ).stdout
    verify_nvram_evidence(evidence.nvram, nvram_output)
    verify_nvram_entry(
        nvram_output,
        label="AnduinOS",
        partuuid=esp.identity.partuuid,
        loader=guided_loader_path(plan),
    )


def _post_install_esp(plan, evidence, partitions):
    if evidence.reused_esp_partuuid:
        for item in partitions:
            if item.identity.partuuid == evidence.reused_esp_partuuid:
                return item
        raise RuntimeError("Reused EFI System Partition disappeared")
    graph = plan.storage.graph
    boot_id = graph.boot_targets[0].efi_filesystem_id
    declaration = next(
        item for item in graph.partitions if item.partition_id == boot_id
    )
    for item in partitions:
        if item.identity.number == declaration.number:
            return item
    raise RuntimeError("New EFI System Partition is missing")


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        plan = read_plan(args.plan)
        validate_plan_for_execution(
            plan, ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST
        )
        require_disposable_guided_vm(
            plan,
            environment=dict(os.environ),
        )
        runner = CommandRunner(
            lambda message: print(message, file=sys.stderr, flush=True)
        )
        if args.action == "capture":
            capture(plan, args.evidence, runner)
        else:
            verify(plan, args.evidence, runner)
        print(json.dumps({"succeeded": True, "action": args.action}))
        return 0
    except Exception as error:
        print(
            json.dumps({"succeeded": False, "error": str(error)}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
