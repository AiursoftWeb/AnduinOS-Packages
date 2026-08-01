#!/bin/bash
set -euo pipefail

readonly TIMEBACK_VM_CONFIRMATION="DESTROY_THIS_DISPOSABLE_VM"
readonly TIMEBACK_VM_STATE_DIR="/.snapshots/anduinos/tm5-vm-test"

die() {
    echo "TM-5 VM qualification refused: $*" >&2
    exit 2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"
}

require_qualification_vm() {
    [ "$(id -u)" -eq 0 ] || die "run as root inside the disposable VM"
    [ "${ANDUINOS_TIMEBACK_VM_CONFIRM:-}" = "$TIMEBACK_VM_CONFIRMATION" ] ||
        die "set ANDUINOS_TIMEBACK_VM_CONFIRM=$TIMEBACK_VM_CONFIRMATION"

    require_command systemd-detect-virt
    require_command timebackctl
    require_command python3
    require_command findmnt
    systemd-detect-virt --vm --quiet || die "the machine is not detected as a VM"
    grep -Eq '(^| )anduinos\.timeback\.test=1( |$)' /proc/cmdline ||
        die "boot the VM with the kernel argument anduinos.timeback.test=1"

    local report
    report="$(timebackctl inspect --json)" || die "Timeback rejected this storage layout"
    printf '%s' "$report" | python3 -c '
import json, sys
report = json.load(sys.stdin)
assert report["support"] == "supported", report
assert len(report["mounts"]) == 6, report
' || die "the exact six-subvolume AnduinOS Btrfs ABI is required"
    [ "$(findmnt --noheadings --output FSTYPE --mountpoint /)" = "btrfs" ] ||
        die "the root filesystem is not Btrfs"
}

latest_id_for_title() {
    local title="$1"
    python3 -c '
import json, sys
title = sys.argv[1]
records = [item for item in json.load(sys.stdin)["deployments"] if item["title"] == title]
if not records:
    raise SystemExit(1)
print(records[0]["id"])
' "$title"
}

deployment_count_for_kind() {
    local kind="$1"
    python3 -c '
import json, sys
kind = sys.argv[1]
print(sum(item["kind"] == kind for item in json.load(sys.stdin)["deployments"]))
' "$kind"
}

assert_clean_discovery() {
    python3 -c '
import json, sys
report = json.load(sys.stdin)
assert not report["issues"], report["issues"]
' || die "deployment discovery contains unresolved metadata issues"
}
