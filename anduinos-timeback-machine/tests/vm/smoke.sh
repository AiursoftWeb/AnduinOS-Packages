#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_qualification_vm
require_command systemctl

title="TM-5 smoke $(date --utc +%Y%m%dT%H%M%SZ)"
deployment_id=""
cleanup() {
    if [ -n "$deployment_id" ]; then
        timebackctl unpin "$deployment_id" >/dev/null 2>&1 || true
        timebackctl delete "$deployment_id" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

echo "[TM-5] Creating, verifying, pinning, and deleting a real recovery point"
timebackctl create --target system "$title"
deployment_id="$(timebackctl list --json | latest_id_for_title "$title")" ||
    die "the newly-created recovery point was not discovered"
timebackctl verify "$deployment_id"
timebackctl pin "$deployment_id"
timebackctl unpin "$deployment_id"
timebackctl delete "$deployment_id"
deployment_id=""

echo "[TM-5] Exercising the fail-open APT pre/post pair"
before_pre="$(timebackctl list --json | deployment_count_for_kind apt-pre)"
before_post="$(timebackctl list --json | deployment_count_for_kind apt-post)"
/usr/libexec/anduinos-timeback-apt-hook pre
/usr/libexec/anduinos-timeback-apt-hook post
report="$(timebackctl list --json)"
printf '%s' "$report" | assert_clean_discovery
after_pre="$(printf '%s' "$report" | deployment_count_for_kind apt-pre)"
after_post="$(printf '%s' "$report" | deployment_count_for_kind apt-post)"
[ "$after_pre" -ge $((before_pre + 1)) ] || die "APT pre recovery point was not retained"
[ "$after_post" -ge $((before_post + 1)) ] || die "APT post recovery point was not retained"
[ ! -e /.snapshots/anduinos/transactions/pending-package.json ] ||
    die "the package transaction remained pending"

echo "[TM-5] Running the periodic maintenance helper and systemd unit"
/usr/libexec/anduinos-timeback-maintenance
systemctl start anduinos-timeback-maintenance.service
systemctl is-enabled --quiet anduinos-timeback-maintenance.timer ||
    die "the periodic maintenance timer is not enabled"

echo "TM-5 guest smoke qualification passed"
