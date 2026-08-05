#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

test -f "$ROOT/src/Cargo.lock"
test -f "$ROOT/upstream/LICENSE"
test -f "$ROOT/upstream/README.md"
test -f "$ROOT/data/org.anduinos.Waypoint.svg"
test -f "$ROOT/data/org.anduinos.Waypoint.metainfo.xml"
test -x "$ROOT/compile-locales.sh"
test -f "$ROOT/po/anduinos-waypoint-gtk.pot"
test -f "$ROOT/data/initramfs-hook"
test -f "$ROOT/data/initramfs-local-premount"
test -f "$ROOT/data/09_anduinos_waypoint"
test -x "$ROOT/data/no-os-prober"
test -f "$ROOT/data/01_anduinos_waypoint_env"
test -f "$ROOT/data/anduinos-waypoint-confirm.service"
test -f "$ROOT/src/anduinos-recovery-engine/src/bin/confirm.rs"
test -f "$ROOT/src/anduinos-recovery-engine/src/bin/apt_hook.rs"
test -f "$ROOT/data/90-anduinos-waypoint"
grep -Fq 'if [ -x /usr/libexec/anduinos-waypoint-apt-hook ]' \
    "$ROOT/data/90-anduinos-waypoint"
test -f "$ROOT/scripts/postrm.sh"
test -f "$ROOT/docs/deployment-v1.schema.json"
test -f "$ROOT/docs/rollback-v1.schema.json"
test -f "$ROOT/docs/external-backup-v1.schema.json"
test -f "$ROOT/docs/VM-QUALIFICATION.md"
test -f "$ROOT/docs/RECOVERY-SCOPE.md"
test -x "$ROOT/scripts/test-external-backup-loopback.sh"
test -x "$ROOT/scripts/test-recovery-operations-loopback.sh"
test -x "$ROOT/scripts/qualify-recovery-vm.sh"
test -x "$ROOT/scripts/test-installed-policy.sh"
test -x "$ROOT/scripts/check-i18n.py"

rg -q 'rm -f -- /etc/anduinos-waypoint/schedules.toml' "$ROOT/scripts/postrm.sh"
rg -q 'systemctl enable anduinos-waypoint-confirm.service' "$ROOT/scripts/postinst.sh"
rg -q 'systemctl disable --now anduinos-waypoint-confirm.service' "$ROOT/scripts/prerm.sh"
grep -Fq 'anduinos-waypoint-confirm.service" AutoEnable="false"' \
    "$ROOT/anduinos-waypoint-gtk.aosproj"
if rg -n 'rm -r[f ]|find .*RECOVERY_STORE|/\.snapshots/anduinos-waypoint' "$ROOT/scripts/postrm.sh"; then
    echo "Package removal must never recursively delete recovery-point data" >&2
    exit 1
fi

python3 "$ROOT/scripts/check-i18n.py"

rg -q '\.arg\("create-scheduled"\)' "$ROOT/src/waypoint-scheduler/src/main.rs"
rg -q 'create-scheduled\) cmd_create_scheduled' "$ROOT/src/waypoint-cli"
rg -q 'CreateScheduledDeployment' "$ROOT/src/waypoint-cli"
rg -Fq ".data[0] | booleans | tostring" "$ROOT/src/waypoint-cli"
rg -Fq 'status) cmd_status' "$ROOT/src/waypoint-cli"
rg -Fq 'create [--json]' "$ROOT/src/waypoint-cli"

if rg -n 'xbps|sudo sv|/var/service|/etc/sv|/etc/waypoint|\.config/waypoint|\.local/share/waypoint|/var/lib/waypoint|tech\.geektoshi\.waypoint|com\.voidlinux\.waypoint|from_icon_name\("waypoint"|set-default|get-default|root-writable|cleanup-writable-snapshots|System rollback is disabled in this development build' \
    "$ROOT/src" --glob '!Cargo.lock'; then
    echo "Void/upstream platform bindings remain in the buildable source" >&2
    exit 1
fi

if rg -n 'BackupSnapshot|RestoreFromBackup|ScanBackupDestinations|ApplyBackupRetention|destination_mount|backup_path|snapshot_path_from_name|RestoreFiles|restore_files|ListSnapshots|list_snapshots|CleanupSnapshots|cleanup_snapshots' \
    "$ROOT/src" --glob '!Cargo.lock' --glob '!target/**'; then
    echo "A removed caller-path privileged ABI remains in buildable source" >&2
    exit 1
fi

if rg -n 'affected_subvolumes|personal_files_affected|restart_required|fallback_preserved|SnapshotInfo|SnapshotTarget|pub mod targets' \
    "$ROOT/src" --glob '!Cargo.lock' --glob '!target/**'; then
    echo "A removed generic/custom recovery-scope model remains in buildable source" >&2
    exit 1
fi

if rg -n 'SnapshotAction::Browse|Browse Files|open_containing_folder' \
    "$ROOT/src" --glob '!target/**'; then
    echo "The root-private recovery store must not be exposed as a desktop browse path" >&2
    exit 1
fi

if rg -n 'Command::new\("(?:stat|df|btrfs)"\)|/\.snapshots/anduinos-waypoint|\bmod cache\b|\bTtlCache\b' \
    "$ROOT/src/waypoint/src" --glob '*.rs'; then
    echo "The desktop UI must not duplicate privileged storage probes or model root-private paths" >&2
    exit 1
fi

if rg -n 'inspect_anduinos_layout' "$ROOT/src/waypoint/src" --glob '*.rs'; then
    echo "The desktop UI must use the helper-owned layout report, not a local layout probe" >&2
    exit 1
fi

if rg -n '/tmp/anduinos-waypoint.*preferences' "$ROOT/src/waypoint/src" --glob '*.rs'; then
    echo "Per-user preferences must never fall back to a shared predictable /tmp path" >&2
    exit 1
fi

rg -q 'CompareDeploymentPackages' "$ROOT/src/waypoint/src/dbus_client.rs"
rg -q 'ApplyScheduleRetention' "$ROOT/src/waypoint-cli"

if rg -n 'Command::new\("(?:/usr/bin/)?(?:apt|apt-get|aptitude|pkcon)"|run_command\("(?:/usr/bin/)?(?:apt|apt-get|aptitude|pkcon)"' \
    "$ROOT/src" --glob '!target/**'; then
    echo "Arbitrary package installation or package-manager execution entered Waypoint" >&2
    exit 1
fi
