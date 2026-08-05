#!/usr/bin/env bash
set -euo pipefail

# Non-destructive installed-package qualification for the D-Bus caller boundary
# and every Waypoint Polkit action. All mutating calls use deliberately invalid
# identifiers or payloads and must fail after authorization without changing
# recovery state.

readonly SERVICE="org.anduinos.Waypoint"
readonly OBJECT="/org/anduinos/Waypoint"
readonly INTERFACE="org.anduinos.Waypoint.Helper"
readonly INVALID_ID="not-a-recovery-point-id"

actions=(
    org.anduinos.waypoint.create-snapshot
    org.anduinos.waypoint.delete-snapshot
    org.anduinos.waypoint.restore-snapshot
    org.anduinos.waypoint.configure-system
    org.anduinos.waypoint.external-backup
    org.anduinos.waypoint.personal-files
)

for command in busctl getent jq pkaction pkcheck sudo; do
    command -v "$command" >/dev/null || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
sudo -n true 2>/dev/null || {
    echo "Passwordless sudo is required for installed-policy qualification" >&2
    exit 77
}

mapfile -t installed_actions < <(pkaction | grep '^org\.anduinos\.waypoint\.' | sort)
mapfile -t expected_actions < <(printf '%s\n' "${actions[@]}" | sort)
[[ "${installed_actions[*]}" == "${expected_actions[*]}" ]] || {
    echo "The installed Waypoint Polkit action set is incomplete or unexpected" >&2
    printf 'installed: %s\n' "${installed_actions[*]}" >&2
    exit 1
}

sudo -n sh -c '
    for action do
        pkcheck --action-id "$action" --process $$ || exit
    done
' sh "${actions[@]}"

status() {
    busctl --system --json=short call \
        "$SERVICE" "$OBJECT" "$INTERFACE" GetRecoveryEngineStatus |
        jq -er '.data[0] | fromjson'
}

assert_safe_failure() {
    local method="$1"
    local signature="$2"
    shift 2
    local response
    response=$(sudo -n busctl --system --json=short call \
        "$SERVICE" "$OBJECT" "$INTERFACE" "$method" "$signature" "$@")
    jq -e '
        .data[0] == false and
        (.data[1] | strings | startswith("Authorization failed:") | not)
    ' <<<"$response" >/dev/null || {
        echo "$method did not reach its authorized, safe validation failure" >&2
        jq . <<<"$response" >&2
        exit 1
    }
}

before=$(status | jq -c '{pending, deployment_count, personal_snapshot_count}')
assert_safe_failure CreateDeployment ssb "" "" false
assert_safe_failure DeleteDeployment s "$INVALID_ID"
assert_safe_failure ScheduleDeploymentRestore s "$INVALID_ID"
assert_safe_failure SetDeploymentPinned sb "$INVALID_ID" false
assert_safe_failure VerifyExternalBackup ss "invalid-filesystem" "$INVALID_ID"
assert_safe_failure DeletePersonalSnapshot s "$INVALID_ID"
assert_safe_failure VerifyPersonalExternalBackup ss "invalid-filesystem" "$INVALID_ID"
after=$(status | jq -c '{pending, deployment_count, personal_snapshot_count}')
[[ "$before" == "$after" ]] || {
    echo "Invalid authorization probes unexpectedly changed recovery state" >&2
    exit 1
}

caller=${SUDO_USER:-$(id -un)}
if id -nG "$caller" | tr ' ' '\n' | grep -qx sudo; then
    sudo -n -u "$caller" env HOME="$(getent passwd "$caller" | cut -d: -f6)" \
        busctl --system call "$SERVICE" "$OBJECT" "$INTERFACE" \
        GetRecoveryEngineStatus >/dev/null
fi

if getent passwd nobody >/dev/null; then
    if sudo -n -u nobody env HOME=/nonexistent \
        busctl --system call "$SERVICE" "$OBJECT" "$INTERFACE" \
        GetRecoveryEngineStatus >/dev/null 2>&1; then
        echo "A non-administrator unexpectedly reached the Waypoint helper" >&2
        exit 1
    fi
fi

introspection=$(busctl --system introspect "$SERVICE" "$OBJECT" "$INTERFACE")
grep -q 'CompareDeploymentPackages' <<<"$introspection"
grep -q 'CreateScheduledDeployment' <<<"$introspection"
grep -q 'ApplyScheduleRetention' <<<"$introspection"
grep -q 'CreateScheduledPersonalSnapshot' <<<"$introspection"
grep -q 'AutomaticSnapshotCreated' <<<"$introspection"
grep -q 'AutomaticSnapshotsDeleted' <<<"$introspection"
grep -q 'ListPersonalFiles' <<<"$introspection"
grep -q 'ExportPersonalFile' <<<"$introspection"
grep -q 'ExportPersonalSnapshot' <<<"$introspection"
if grep -q 'CleanupSnapshots' <<<"$introspection"; then
    echo "The obsolete generic retention method is still installed" >&2
    exit 1
fi

echo "Installed D-Bus caller and Polkit action qualification passed"
