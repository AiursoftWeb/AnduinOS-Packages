#!/usr/bin/env bash
set -euo pipefail

# Destructive, reboot-spanning qualification helper for a disposable VM made
# by the AnduinOS installer. It deliberately refuses physical machines,
# containers, unsupported layouts, and implicit destructive execution.

readonly CLI="${ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_CLI:-/usr/bin/anduinos-btrfs-snapshots-manager-cli}"
readonly STATE_DIR="/var/log/anduinos-btrfs-snapshots-manager-qualification"
readonly STATE_FILE="$STATE_DIR/state.json"
readonly MARKER_FILE="/etc/anduinos-btrfs-snapshots-manager-qualification-marker"
readonly RECOVERY_STORE="/.snapshots/anduinos-btrfs-snapshots-manager"
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
  sudo $0 prepare-docker-autoremove $CONSENT
  sudo $0 verify-docker-autoremove
  sudo $0 test-cancel $CONSENT

prepare-rollback changes /etc inside a disposable VM, creates a system snapshot,
changes the marker again, and arms a one-shot rollback. Reboot the VM only after
the command succeeds. Qualification state is kept on the excluded @log
subvolume so it survives both successful rollback and automatic fallback.

prepare-docker-autoremove requires the distro docker.io package to be installed,
captures its exact version in a system snapshot, runs apt-get autoremove docker.io,
verifies that Docker is absent, and arms restoration of that snapshot.
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
    local status kernel_release initramfs listing
    status=$(status_json)
    kernel_release=$(tr -d '\n' </proc/sys/kernel/osrelease)
    initramfs="/boot/initrd.img-$kernel_release"
    [[ -f "$initramfs" ]] || die "the running kernel's initramfs is missing"
    [[ $(/usr/libexec/anduinos-btrfs-snapshots-manager-initramfs --protocol-version) == 2 ]] ||
        die "the installed recovery engine protocol is incompatible"
    if findmnt -n -o OPTIONS --target /run | tr ',' '\n' | grep -Fxq noexec; then
        echo "Confirmed: /run is noexec; the recovery confirmation regression is active"
    else
        echo "NOTE: /run is executable; repeat the release lane once with /run mounted noexec" >&2
    fi
    findmnt -n -o OPTIONS --target /.snapshots | tr ',' '\n' | grep -Fxq noexec &&
        die "the snapshot store is noexec and cannot host the bound confirmation artifact"
    command -v lsinitramfs >/dev/null || die "lsinitramfs is required"
    listing=$(lsinitramfs "$initramfs") ||
        die "the installed initramfs could not be inspected"
    for member in \
        scripts/local-premount/anduinos-btrfs-snapshots-manager \
        usr/libexec/anduinos-btrfs-snapshots-manager-initramfs \
        usr/libexec/anduinos-btrfs-snapshots-manager-confirm \
        etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version \
        usr/bin/cat usr/bin/chmod usr/bin/cp usr/bin/ln usr/bin/mkdir; do
        grep -Fxq "$member" <<<"$listing" ||
            die "the installed initramfs is missing $member"
    done
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
    printf '%s  %s\n' \
        "$(jq -er '.recovery_kernel_sha256' <<<"$pending")" \
        "$RECOVERY_STORE/recovery-boot/vmlinuz" | sha256sum --check --status ||
        die "the snapshot-external recovery kernel does not match the transaction"
    printf '%s  %s\n' \
        "$(jq -er '.recovery_initramfs_sha256' <<<"$pending")" \
        "$RECOVERY_STORE/recovery-boot/initrd.img" | sha256sum --check --status ||
        die "the snapshot-external recovery initramfs does not match the transaction"
    printf '%s  %s\n' \
        "$(jq -er '.recovery_confirm_sha256' <<<"$pending")" \
        "$RECOVERY_STORE/recovery-boot/confirm" | sha256sum --check --status ||
        die "the snapshot-external confirmation engine does not match the transaction"
    [[ -x "$RECOVERY_STORE/recovery-boot/confirm" ]] ||
        die "the snapshot-external confirmation engine is not executable"
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
    systemctl is-failed --quiet anduinos-btrfs-snapshots-manager-confirm.service &&
        die "the userspace confirmation service failed"
    journalctl -b -u anduinos-btrfs-snapshots-manager-confirm.service --no-pager |
        grep -Fq 'status=203/EXEC' &&
        die "the userspace confirmation engine could not be executed"
    jq -e '.pending == null' <<<"$status" >/dev/null || die "the rollback is still pending"
    jq -e --arg target "$target" \
        '.deployments[] | select(.id == $target and .state == "ready")' \
        <<<"$status" >/dev/null || die "the restored deployment is not reusable and ready"
    local transaction_id archive
    transaction_id=$(jq -er '.pending.id' <<<"$state")
    archive="$RECOVERY_STORE/rollback-history/$transaction_id.json"
    jq -e --arg target "$target" \
        '.phase == "confirmed" and .target_deployment_id == $target and
         .checkpoint == "booted-unconfirmed-recorded" and .initramfs_attempts >= 1' \
        "$archive" >/dev/null || die "the durable rollback history is missing or incomplete"

    state=$(jq '.phase = "verified"' <<<"$state")
    write_state "$state"
    echo "Rebooting rollback qualification passed for $target"
}

prepare_docker_autoremove() {
    [[ "${1:-}" == "$CONSENT" ]] || die "explicit destructive-test consent token is required"
    require_environment
    command -v apt-get >/dev/null || die "apt-get is required"
    command -v docker >/dev/null || die "Docker must be installed before this lane"

    local status package_version run_id created target state pending
    status=$(status_json)
    jq -e '.pending == null' <<<"$status" >/dev/null || die "another rollback is already pending"
    [[ $(dpkg-query -W -f='${db:Status-Status}' docker.io 2>/dev/null) == installed ]] ||
        die "the docker.io package must be installed"
    package_version=$(dpkg-query -W -f='${Version}' docker.io)
    run_id=$(tr -d '\n' </proc/sys/kernel/random/uuid)
    created=$("$CLI" create --json "Docker autoremove rollback $run_id" \
        "Restore docker.io after apt autoremove")
    target=$(jq -er '.id | strings' <<<"$created")

    apt-get autoremove --yes docker.io
    if [[ $(dpkg-query -W -f='${db:Status-Status}' docker.io 2>/dev/null || true) == installed ]]; then
        die "apt autoremove did not remove docker.io"
    fi
    command -v docker >/dev/null && die "the Docker CLI still exists after autoremove"

    printf 'y\n' | "$CLI" restore "$target"
    status=$(status_json)
    pending=$(jq -ec --arg target "$target" \
        '.pending | select(.target_deployment_id == $target and .phase == "armed")' \
        <<<"$status") || die "the Docker rollback transaction was not armed"
    state=$(jq -n \
        --arg run_id "$run_id" \
        --arg target "$target" \
        --arg version "$package_version" \
        --argjson pending "$pending" \
        '{schema_version: 1, scenario: "docker-autoremove", phase: "armed",
          run_id: $run_id, target_deployment_id: $target,
          expected_docker_io_version: $version, pending: $pending}')
    write_state "$state"
    sync
    echo "docker.io was removed and rollback $target is armed"
    echo "Reboot this VM, then run: sudo $0 verify-docker-autoremove"
}

verify_docker_autoremove() {
    require_environment
    [[ -f "$STATE_FILE" ]] || die "qualification state is missing"
    local state target expected_version actual_version status transaction_id archive
    state=$(<"$STATE_FILE")
    jq -e '.scenario == "docker-autoremove" and .phase == "armed"' \
        <<<"$state" >/dev/null || die "qualification state is not a pending Docker lane"
    target=$(jq -er '.target_deployment_id' <<<"$state")
    expected_version=$(jq -er '.expected_docker_io_version' <<<"$state")
    actual_version=$(dpkg-query -W -f='${Version}' docker.io 2>/dev/null) ||
        die "docker.io was not restored"
    [[ "$actual_version" == "$expected_version" ]] || die "docker.io version was not restored"
    [[ -x /usr/bin/docker ]] || die "/usr/bin/docker was not restored"

    status=$(status_json)
    jq -e '.pending == null' <<<"$status" >/dev/null || die "the Docker rollback is still pending"
    jq -e --arg target "$target" \
        '.deployments[] | select(.id == $target and .state == "ready")' \
        <<<"$status" >/dev/null || die "the Docker rollback target is not reusable and ready"
    transaction_id=$(jq -er '.pending.id' <<<"$state")
    archive="$RECOVERY_STORE/rollback-history/$transaction_id.json"
    jq -e '.phase == "confirmed" and .checkpoint == "booted-unconfirmed-recorded"' \
        "$archive" >/dev/null || die "the Docker rollback diagnostic history is incomplete"
    state=$(jq '.phase = "verified"' <<<"$state")
    write_state "$state"
    echo "Docker autoremove rollback qualification passed for $target"
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
    prepare-docker-autoremove) prepare_docker_autoremove "${2:-}" ;;
    verify-docker-autoremove) verify_docker_autoremove ;;
    test-cancel) test_cancel "${2:-}" ;;
    -h|--help|help|"") usage ;;
    *) usage >&2; exit 64 ;;
esac
