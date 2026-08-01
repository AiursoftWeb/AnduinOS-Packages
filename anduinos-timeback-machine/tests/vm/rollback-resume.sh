#!/bin/bash
set -euo pipefail

readonly STATE_DIR="/.snapshots/anduinos/tm5-vm-test"
readonly STATE_FILE="$STATE_DIR/state.json"
readonly EXPECTATION_FWCFG="/sys/firmware/qemu_fw_cfg/by_name/opt/anduinos/timeback-expected/raw"

fail() {
    echo "TM-5 rollback qualification failed: $*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || fail "resume verifier is not root"
systemd-detect-virt --vm --quiet || fail "resume verifier is not running in a VM"
grep -Eq '(^| )anduinos\.timeback\.test=1( |$)' /proc/cmdline ||
    fail "qualification kernel marker is absent"
[ -f "$STATE_FILE" ] || fail "persistent qualification state is missing"

readarray -t state < <(python3 - "$STATE_FILE" <<'PY'
import json, sys, uuid
with open(sys.argv[1], encoding="utf-8") as stream:
    state = json.load(stream)
assert state["phase"] == "scheduled"
uuid.UUID(state["token"])
uuid.UUID(state["deployment_id"])
print(state["token"])
print(state["deployment_id"])
PY
)
[ "${#state[@]}" -eq 2 ] || fail "qualification state is malformed"
token="${state[0]}"
deployment_id="${state[1]}"

expectation="target"
if [ -f "$EXPECTATION_FWCFG" ]; then
    expectation="$(tr -d '\000\r\n' < "$EXPECTATION_FWCFG")"
fi
case "$expectation" in
target)
    expected_root="target:$token"
    expected_state="current"
    ;;
fallback)
    expected_root="new-root:$token"
    expected_state="failed-reverted"
    ;;
*)
    fail "host supplied an unknown result expectation"
    ;;
esac

[ ! -e /.snapshots/anduinos/transactions/pending-rollback.json ] ||
    fail "rollback confirmation did not clear the pending transaction"
[ "$(cat /etc/anduinos-timeback-tm5-root)" = "$expected_root" ] ||
    fail "the expected @root was not activated"
for marker in \
    /home/anduinos-timeback-tm5-persistent \
    /var/log/anduinos-timeback-tm5-persistent \
    /.snapshots/anduinos-timeback-tm5-persistent \
    /var/lib/containers/anduinos-timeback-tm5-persistent \
    /var/lib/libvirt/images/anduinos-timeback-tm5-persistent; do
    [ "$(cat "$marker")" = "$token" ] || fail "persistent boundary changed: $marker"
done

timebackctl inspect --json | python3 -c '
import json, sys
report = json.load(sys.stdin)
assert report["support"] == "supported", report
assert len(report["mounts"]) == 6, report
' || fail "the restored system no longer has the complete Btrfs ABI"

timebackctl list --json | python3 -c '
import json, sys
deployment_id = sys.argv[1]
expected_state = sys.argv[2]
matches = [item for item in json.load(sys.stdin)["deployments"] if item["id"] == deployment_id]
assert len(matches) == 1, matches
assert matches[0]["state"] == expected_state, matches[0]
' "$deployment_id" "$expected_state" || fail "the target deployment has the wrong terminal state"

python3 - "$STATE_FILE" "$expectation" <<'PY'
import json, os, sys
path, expectation = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    state = json.load(stream)
state["phase"] = "passed-" + expectation
temporary = path + ".new"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(state, stream)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, path)
PY

rm -f \
    /etc/anduinos-timeback-tm5-root \
    /home/anduinos-timeback-tm5-persistent \
    /var/log/anduinos-timeback-tm5-persistent \
    /.snapshots/anduinos-timeback-tm5-persistent \
    /var/lib/containers/anduinos-timeback-tm5-persistent \
    /var/lib/libvirt/images/anduinos-timeback-tm5-persistent
systemctl disable anduinos-timeback-vm-resume.service
rm -f /etc/systemd/system/anduinos-timeback-vm-resume.service
rm -f /usr/local/libexec/anduinos-timeback-vm-resume
systemctl daemon-reload

echo "TM-5-RESULT $expectation passed; persistent result: $STATE_FILE"
