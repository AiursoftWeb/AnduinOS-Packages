#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_qualification_vm
require_command install
require_command systemctl
require_command uuidgen

[ ! -e /.snapshots/anduinos/transactions/pending-rollback.json ] ||
    die "a rollback transaction is already pending"
[ ! -e /.snapshots/anduinos/transactions/pending-package.json ] ||
    die "a package transaction is already pending"

armed=0
deployment_id=""
cleanup_unarmed() {
    if [ "$armed" -ne 0 ]; then
        return
    fi
    if [ -n "$deployment_id" ]; then
        timebackctl delete "$deployment_id" >/dev/null 2>&1 || true
    fi
    systemctl disable anduinos-timeback-vm-resume.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/anduinos-timeback-vm-resume.service
    rm -f /usr/local/libexec/anduinos-timeback-vm-resume
    rm -f \
        /etc/anduinos-timeback-tm5-root \
        /home/anduinos-timeback-tm5-persistent \
        /var/log/anduinos-timeback-tm5-persistent \
        /.snapshots/anduinos-timeback-tm5-persistent \
        /var/lib/containers/anduinos-timeback-tm5-persistent \
        /var/lib/libvirt/images/anduinos-timeback-tm5-persistent \
        "$TIMEBACK_VM_STATE_DIR/state.json" \
        "$TIMEBACK_VM_STATE_DIR/state.json.new"
    rmdir "$TIMEBACK_VM_STATE_DIR" >/dev/null 2>&1 || true
    systemctl daemon-reload >/dev/null 2>&1 || true
}
trap cleanup_unarmed EXIT

install -D -m 0755 "$SCRIPT_DIR/rollback-resume.sh" \
    /usr/local/libexec/anduinos-timeback-vm-resume
install -D -m 0644 "$SCRIPT_DIR/anduinos-timeback-vm-resume.service" \
    /etc/systemd/system/anduinos-timeback-vm-resume.service
systemctl daemon-reload
systemctl enable anduinos-timeback-vm-resume.service

token="$(uuidgen)"
title="TM-5 rollback target $token"
mkdir -p "$TIMEBACK_VM_STATE_DIR" /var/lib/containers /var/lib/libvirt/images
chmod 0700 "$TIMEBACK_VM_STATE_DIR"

printf 'target:%s\n' "$token" > /etc/anduinos-timeback-tm5-root
printf '%s\n' "$token" > /home/anduinos-timeback-tm5-persistent
printf '%s\n' "$token" > /var/log/anduinos-timeback-tm5-persistent
printf '%s\n' "$token" > /.snapshots/anduinos-timeback-tm5-persistent
printf '%s\n' "$token" > /var/lib/containers/anduinos-timeback-tm5-persistent
printf '%s\n' "$token" > /var/lib/libvirt/images/anduinos-timeback-tm5-persistent

python3 - "$TIMEBACK_VM_STATE_DIR/state.json" "$token" "$title" <<'PY'
import json, os, sys
path, token, title = sys.argv[1:]
temporary = path + ".new"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump({"phase": "preparing", "token": token, "title": title}, stream)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, path)
directory = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
os.fsync(directory)
os.close(directory)
PY

echo "[TM-5] Creating the rollback target"
timebackctl create "$title"
deployment_id="$(timebackctl list --json | latest_id_for_title "$title")" ||
    die "the rollback target was not discovered"

python3 - "$TIMEBACK_VM_STATE_DIR/state.json" "$deployment_id" <<'PY'
import json, os, sys
path, deployment_id = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    state = json.load(stream)
state.update({"phase": "target-created", "deployment_id": deployment_id})
temporary = path + ".new"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(state, stream)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, path)
PY

printf 'new-root:%s\n' "$token" > /etc/anduinos-timeback-tm5-root
echo "[TM-5] Scheduling the verified one-shot rollback"
timebackctl restore "$deployment_id"
armed=1

python3 - "$TIMEBACK_VM_STATE_DIR/state.json" <<'PY'
import json, os, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    state = json.load(stream)
state["phase"] = "scheduled"
temporary = path + ".new"
with open(temporary, "x", encoding="utf-8") as stream:
    json.dump(state, stream)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary, path)
PY

echo "TM-5 rollback is armed. The next boot must enter the Timeback recovery entry."
if [ "${1:-}" = "--reboot" ]; then
    echo "Rebooting the disposable VM now."
    systemctl reboot
else
    echo "Run 'systemctl reboot' when ready; verification will resume automatically."
fi
