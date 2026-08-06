#!/usr/bin/env bash
set -euo pipefail

# Destructive, reboot-spanning qualification helper for a disposable VM made
# by the AnduinOS installer. It deliberately refuses physical machines,
# containers, unsupported layouts, and implicit destructive execution.

readonly CLI="${ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_CLI:-/usr/bin/anduinos-btrfs-snapshots-manager-cli}"
readonly STATE_DIR="/var/log/anduinos-btrfs-snapshots-manager-qualification"
readonly STATE_FILE="$STATE_DIR/state.json"
readonly MARKER_FILE="/etc/anduinos-btrfs-snapshots-manager-qualification-marker"
readonly CONSENT="I_UNDERSTAND_THIS_WILL_ROLL_BACK_THE_VM"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<EOF
Usage:
  sudo $0 preflight
  sudo $0 prepare-rollback $CONSENT
  sudo $0 verify-rollback
  sudo $0 test-cancel $CONSENT

prepare-rollback changes /etc inside a disposable VM, creates a recovery point,
changes the marker again, and arms a one-shot rollback. Reboot the VM only after
the command succeeds. Qualification state is kept on the excluded @log
subvolume so it survives both successful rollback and automatic fallback.
EOF
}

require_environment() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "run this qualification helper as root"
    command -v jq >/dev/null || die "jq is required"
    command -v findmnt >/dev/null || die "findmnt is required"
    command -v systemd-detect-virt >/dev/null || die "systemd-detect-virt is required"
    [[ -x "$CLI" ]] || die "the installed Disk Snapshots Manager CLI is unavailable"
    systemd-detect-virt --vm --quiet || die "refusing to run outside a virtual machine"

    local status root_device log_device log_root
    status=$("$CLI" status --json)
    jq -e '.available == true and .layout.support == "supported"' \
        <<<"$status" >/dev/null || die "the exact installer-created Btrfs layout is required"
    root_device=$(findmnt -n -o MAJ:MIN --target /)
    log_device=$(findmnt -n -o MAJ:MIN --target /var/log)
    log_root=$(findmnt -n -o FSROOT --target /var/log)
    [[ "$log_device" == "$root_device" ]] || die "/var/log is not on the root Btrfs filesystem"
    [[ "$log_root" == "/@log" ]] || die "/var/log is not the independent @log subvolume"
}

status_json() {
    "$CLI" status --json
}

write_state() {
    local payload="$1"
    local temporary
    install -d -m 0700 "$STATE_DIR"
    temporary=$(mktemp "$STATE_DIR/.state.XXXXXX")
    printf '%s\n' "$payload" >"$temporary"
    chmod 0600 "$temporary"
    mv -f "$temporary" "$STATE_FILE"
    sync "$STATE_DIR"
}

preflight() {
    require_environment
    local status
    status=$(status_json)
    jq '{available, layout, pending, deployment_count, issues}' <<<"$status"
    echo "VM and fixed-layout preflight passed"
}

prepare_rollback() {
    [[ "${1:-}" == "$CONSENT" ]] || die "explicit destructive-test consent token is required"
    require_environment

    local status run_id baseline created target state pending
    status=$(status_json)
    jq -e '.pending == null' <<<"$status" >/dev/null || die "another rollback is already pending"

    run_id=$(tr -d '\n' </proc/sys/kernel/random/uuid)
    baseline="btrfs-snapshots-manager-baseline-$run_id"
    printf '%s\n' "$baseline" >"$MARKER_FILE"
    chmod 0644 "$MARKER_FILE"
    sync "$MARKER_FILE"

    created=$("$CLI" create --json "VM qualification $run_id" \
        "Disposable VM rollback qualification")
    target=$(jq -er '.id | strings' <<<"$created")
    state=$(jq -n \
        --arg run_id "$run_id" \
        --arg baseline "$baseline" \
        --arg target "$target" \
        '{schema_version: 1, phase: "point-created", run_id: $run_id,
          expected_marker: $baseline, target_deployment_id: $target}')
    write_state "$state"

    printf 'btrfs-snapshots-manager-mutated-%s\n' "$run_id" >"$MARKER_FILE"
    sync "$MARKER_FILE"
    printf 'y\n' | "$CLI" restore "$target"

    status=$(status_json)
    pending=$(jq -ec --arg target "$target" \
        '.pending | select(.target_deployment_id == $target and .phase == "armed")' \
        <<<"$status") || die "the rollback transaction was not armed"
    state=$(jq --arg phase "armed" --argjson pending "$pending" \
        '.phase = $phase | .pending = $pending' <<<"$state")
    write_state "$state"
    sync

    echo "Rollback transaction armed for target $target"
    echo "Reboot this disposable VM, then run: sudo $0 verify-rollback"
}

verify_rollback() {
    require_environment
    [[ -f "$STATE_FILE" ]] || die "qualification state is missing"

    local state target expected status actual
    state=$(<"$STATE_FILE")
    target=$(jq -er '.target_deployment_id | strings' <<<"$state")
    expected=$(jq -er '.expected_marker | strings' <<<"$state")
    actual=$(tr -d '\n' <"$MARKER_FILE")
    [[ "$actual" == "$expected" ]] || die "the restored marker is wrong; fallback or rollback failure occurred"

    status=$(status_json)
    jq -e '.pending == null' <<<"$status" >/dev/null || die "the rollback is still pending"
    jq -e --arg target "$target" \
        '.deployments[] | select(.id == $target and .state == "current")' \
        <<<"$status" >/dev/null || die "the restored deployment was not confirmed as current"

    state=$(jq '.phase = "verified"' <<<"$state")
    write_state "$state"
    echo "Rebooting rollback qualification passed for $target"
}

test_cancel() {
    [[ "${1:-}" == "$CONSENT" ]] || die "explicit destructive-test consent token is required"
    require_environment

    local status run_id created target fallback
    status=$(status_json)
    jq -e '.pending == null' <<<"$status" >/dev/null || die "another rollback is already pending"
    run_id=$(tr -d '\n' </proc/sys/kernel/random/uuid)
    created=$("$CLI" create --json "VM cancellation $run_id" \
        "Disposable VM cancellation qualification")
    target=$(jq -er '.id | strings' <<<"$created")
    printf 'y\n' | "$CLI" restore "$target"
    status=$(status_json)
    fallback=$(jq -er --arg target "$target" \
        '.pending | select(.target_deployment_id == $target and .phase == "armed") |
         .fallback_deployment_id' <<<"$status")
    "$CLI" cancel-restore
    status=$(status_json)
    jq -e --arg target "$target" --arg fallback "$fallback" '
        .pending == null and
        any(.deployments[]; .id == $target and .state == "ready") and
        any(.deployments[]; .id == $fallback and .state == "ready")
    ' <<<"$status" >/dev/null || die "cancel did not restore both deployment states"
    echo "Pre-reboot cancellation qualification passed"
}

case "${1:-}" in
    preflight) preflight ;;
    prepare-rollback) prepare_rollback "${2:-}" ;;
    verify-rollback) verify_rollback ;;
    test-cancel) test_cancel "${2:-}" ;;
    -h|--help|help|"") usage ;;
    *) usage >&2; exit 64 ;;
esac
