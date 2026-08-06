#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

test -f "$ROOT/src/Cargo.lock"
test -f "$ROOT/upstream/LICENSE"
test -f "$ROOT/upstream/README.md"
test -f "$ROOT/data/org.anduinos.Waypoint.svg"
# Waypoint deliberately retains the commissioned Timeback application artwork.
# Resource identifiers may change with the product name; the artwork may not.
echo 'f6d678d9551cbeb64c4fcad189d1b34aaaad59465588eee7b504cd0c798729a3  '"$ROOT/data/org.anduinos.Waypoint.svg" \
    | sha256sum --check --status
test -f "$ROOT/data/org.anduinos.Waypoint.metainfo.xml"
test -f "$ROOT/data/org.anduinos.Waypoint.Notifier.desktop"
test -f "$ROOT/data/org.anduinos.Waypoint.Session.service"
test -f "$ROOT/data/anduinos_waypoint_file_history.py"
test -x "$ROOT/compile-locales.sh"
test -f "$ROOT/po/anduinos-waypoint-gtk.pot"
test -f "$ROOT/data/initramfs-hook"
test -f "$ROOT/data/initramfs-local-premount"
test -f "$ROOT/data/09_anduinos_waypoint"
test -x "$ROOT/data/no-os-prober"
test -f "$ROOT/data/01_anduinos_waypoint_env"
test -f "$ROOT/data/anduinos-waypoint-confirm.service"
test -f "$ROOT/src/anduinos-recovery-engine/src/waypoint_initramfs.rs"
test -f "$ROOT/src/anduinos-recovery-engine/src/waypoint_boot_config.rs"
test -f "$ROOT/src/anduinos-recovery-engine/src/waypoint_confirm.rs"
test -f "$ROOT/src/anduinos-recovery-engine/src/waypoint_apt_hook.rs"
test -f "$ROOT/src/waypoint-notifier/src/main.rs"
grep -Fq 'obj/anduinos-waypoint-notifier" Target="/usr/libexec/anduinos-waypoint-notifier"' \
    "$ROOT/anduinos-waypoint-gtk.aosproj"
grep -Fq 'Target="/etc/xdg/autostart/org.anduinos.Waypoint.Notifier.desktop"' \
    "$ROOT/anduinos-waypoint-gtk.aosproj"
if rg -n 'path[[:space:]]*=[[:space:]]*"src/bin/' \
    "$ROOT/src" --glob 'Cargo.toml'; then
    echo "Executable Rust sources must use the repository-standard src/*.rs layout" >&2
    exit 1
fi
test -f "$ROOT/data/90-anduinos-waypoint"
grep -Fq 'if [ -x /usr/libexec/anduinos-waypoint-apt-hook ]' \
    "$ROOT/data/90-anduinos-waypoint"
test -f "$ROOT/scripts/postrm.sh"
test -f "$ROOT/docs/deployment-v1.schema.json"
test -f "$ROOT/docs/rollback-v1.schema.json"
test -f "$ROOT/docs/external-backup-v1.schema.json"
test -f "$ROOT/docs/personal-snapshot-v1.schema.json"
test -f "$ROOT/docs/personal-backup-v1.schema.json"
test -f "$ROOT/docs/VM-QUALIFICATION.md"
test -f "$ROOT/docs/RECOVERY-SCOPE.md"
test -x "$ROOT/scripts/test-external-backup-loopback.sh"
test -x "$ROOT/scripts/test-recovery-operations-loopback.sh"
test -x "$ROOT/scripts/qualify-recovery-vm.sh"
test -x "$ROOT/scripts/test-installed-policy.sh"
test -x "$ROOT/scripts/check-i18n.py"
test -x "$ROOT/scripts/screenshot-demo-service.py"
python3 - "$ROOT/scripts/screenshot-demo-service.py" <<'PY'
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    compile(source.read(), sys.argv[1], "exec")
PY
python3 - "$ROOT/data/anduinos_waypoint_file_history.py" <<'PY'
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    compile(source.read(), sys.argv[1], "exec")
PY
grep -Fq '<Dependency Include="python3-nautilus"' \
    "$ROOT/anduinos-waypoint-gtk.aosproj"
grep -Fq 'Target="/usr/share/nautilus-python/extensions/anduinos_waypoint_file_history.py"' \
    "$ROOT/anduinos-waypoint-gtk.aosproj"
grep -Fq 'Target="/usr/share/dbus-1/services/org.anduinos.Waypoint.service"' \
    "$ROOT/anduinos-waypoint-gtk.aosproj"
grep -Fq 'Gio.BusType.SESSION' "$ROOT/data/anduinos_waypoint_file_history.py"
grep -Fq 'def get_file_items' "$ROOT/data/anduinos_waypoint_file_history.py"
grep -Fq 'def get_background_items' "$ROOT/data/anduinos_waypoint_file_history.py"
grep -Fq 'View File History…' "$ROOT/data/anduinos_waypoint_file_history.py"
grep -Fq 'Browse This Folder’s History…' "$ROOT/data/anduinos_waypoint_file_history.py"
grep -Fq 'SimpleAction::new("file-history"' "$ROOT/src/waypoint/src/main.rs"
grep -Fq 'Exec=/usr/bin/anduinos-waypoint-gtk --gapplication-service' \
    "$ROOT/data/org.anduinos.Waypoint.Session.service"
if rg -n 'BusType\.SYSTEM|subprocess|os\.system|Popen|anduinos-waypoint-helper' \
    "$ROOT/data/anduinos_waypoint_file_history.py"; then
    echo "The Nautilus extension must not spawn or contact privileged services" >&2
    exit 1
fi
python3 - "$ROOT/screenshots/overview.png" "$ROOT/screenshots/scheduled-recovery.png" <<'PY'
import struct
import sys

for name in sys.argv[1:]:
    with open(name, "rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise SystemExit(f"AppStream screenshot is not a PNG: {name}")
        length, kind = struct.unpack(">I4s", stream.read(8))
        if length != 13 or kind != b"IHDR":
            raise SystemExit(f"AppStream screenshot has no valid IHDR: {name}")
        width, height = struct.unpack(">II", stream.read(8))
        if (width, height) != (1280, 720):
            raise SystemExit(f"AppStream screenshot must be 1280x720: {name}")
PY

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

rg -q 'ScheduleScope::System => "create-scheduled"' "$ROOT/src/waypoint-scheduler/src/main.rs"
rg -q 'ScheduleScope::Personal => "personal-create-scheduled"' "$ROOT/src/waypoint-scheduler/src/main.rs"
rg -q 'create-scheduled\) cmd_create_scheduled' "$ROOT/src/waypoint-cli"
rg -q 'CreateScheduledDeployment' "$ROOT/src/waypoint-cli"
rg -q 'CreateScheduledPersonalSnapshot' "$ROOT/src/waypoint-cli"
rg -q 'notify_on_create' "$ROOT/src/waypoint-common/src/schedules.rs"
rg -q 'AutomaticSnapshotCreated' "$ROOT/src/waypoint-notifier/src/main.rs"
rg -q 'AutomaticSnapshotsDeleted' "$ROOT/src/waypoint-notifier/src/main.rs"
if rg -n 'notify-send|org\.freedesktop\.Notifications' \
    "$ROOT/src/waypoint-helper/src" "$ROOT/src/waypoint-scheduler/src"; then
    echo "Privileged services must not send desktop-session notifications directly" >&2
    exit 1
fi
if rg -n 'RECOVERY_STORE_ROOT|/\.snapshots|ListPersonalFiles|ExportPersonalFile' \
    "$ROOT/src/waypoint-notifier/src"; then
    echo "The desktop notifier must not gain recovery-store or file-browsing capabilities" >&2
    exit 1
fi
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
rg -q 'ExportPersonalFile' "$ROOT/src/waypoint/src/dbus_client.rs"

if rg -n 'Command::new\("(?:/usr/bin/)?(?:apt|apt-get|aptitude|pkcon)"|run_command\("(?:/usr/bin/)?(?:apt|apt-get|aptitude|pkcon)"' \
    "$ROOT/src" --glob '!target/**'; then
    echo "Arbitrary package installation or package-manager execution entered Waypoint" >&2
    exit 1
fi
