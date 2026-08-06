#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

test -f "$ROOT/src/Cargo.lock"
test -f "$ROOT/../LICENSE"
test -f "$ROOT/LICENSE.upstream-MIT"
test ! -e "$ROOT/upstream"
grep -Fq 'GNU GENERAL PUBLIC LICENSE' "$ROOT/../LICENSE"
grep -Fq 'MIT License' "$ROOT/LICENSE.upstream-MIT"
grep -Fq '<LicenseType>GPL-3.0-or-later</LicenseType>' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'license = "GPL-3.0-or-later"' "$ROOT/src/Cargo.toml"
grep -Fq '<project_license>GPL-3.0-or-later</project_license>' \
    "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.metainfo.xml"
retired_name='way''point'
if rg -ni "$retired_name" "$ROOT" \
    --glob '!LICENSE.upstream-MIT' --glob '!README.md' \
    --glob '!target/**' --glob '!obj/**' --glob '!bin/**'; then
    echo "The retired product name remains outside the required upstream license attribution" >&2
    exit 1
fi
retired_snapshot_terms='recovery[- ]points?|history[- ]points?|恢复''点|历史''点'
if rg -ni "$retired_snapshot_terms" "$ROOT" \
    --glob '!target/**' --glob '!obj/**' --glob '!bin/**' \
    | grep -vF '"automatic-recovery-points"'; then
    echo "Retired snapshot terminology remains in the product" >&2
    exit 1
fi
test -f "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.svg"
# The product rename must not alter the AnduinOS-owned application artwork.
echo 'f6d678d9551cbeb64c4fcad189d1b34aaaad59465588eee7b504cd0c798729a3  '"$ROOT/data/org.anduinos.BtrfsSnapshotsManager.svg" \
    | sha256sum --check --status
test -f "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.metainfo.xml"
for keyword in snapshot restore btrfs backup; do
    grep -Eq "^Keywords=([^;]+;)*${keyword};" \
        "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.desktop"
    grep -Eq "^Keywords\[zh_CN\]=([^;]+;)*${keyword};" \
        "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.desktop"
    grep -Fq "<keyword>${keyword}</keyword>" \
        "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.metainfo.xml"
done
for keyword in 快照 备份 还原 恢复; do
    grep -Eq "^Keywords\[zh_CN\]=([^;]+;)*${keyword};" \
        "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.desktop"
done
test -f "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.Notifier.desktop"
test -f "$ROOT/data/anduinos-btrfs-snapshots-manager-notifier.service"
test -f "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.Session.service"
test -f "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py"
test -x "$ROOT/compile-locales.sh"
test -f "$ROOT/po/anduinos-btrfs-snapshots-manager.pot"
test -f "$ROOT/data/initramfs-hook"
test -f "$ROOT/data/initramfs-local-premount"
test -f "$ROOT/data/09_anduinos_btrfs_snapshots_manager"
test -x "$ROOT/data/no-os-prober"
test -f "$ROOT/data/01_anduinos_btrfs_snapshots_manager_env"
test -f "$ROOT/data/anduinos-btrfs-snapshots-manager-confirm.service"
test -f "$ROOT/src/anduinos-recovery-engine/src/btrfs_snapshots_manager_initramfs.rs"
test -f "$ROOT/src/anduinos-recovery-engine/src/btrfs_snapshots_manager_boot_config.rs"
test -f "$ROOT/src/anduinos-recovery-engine/src/btrfs_snapshots_manager_confirm.rs"
test -f "$ROOT/src/anduinos-recovery-engine/src/btrfs_snapshots_manager_apt_hook.rs"
test -f "$ROOT/src/btrfs-snapshots-manager-notifier/src/main.rs"
grep -Fq 'obj/anduinos-btrfs-snapshots-manager-notifier" Target="/usr/libexec/anduinos-btrfs-snapshots-manager-notifier"' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'Target="/etc/xdg/autostart/org.anduinos.BtrfsSnapshotsManager.Notifier.desktop"' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'Target="/usr/lib/systemd/user/anduinos-btrfs-snapshots-manager-notifier.service"' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'Exec=/usr/bin/systemctl --user start --no-block anduinos-btrfs-snapshots-manager-notifier.service' \
    "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.Notifier.desktop"
grep -Fq 'Type=dbus' "$ROOT/data/anduinos-btrfs-snapshots-manager-notifier.service"
grep -Fq 'BusName=org.anduinos.BtrfsSnapshotsManager.Notifier' \
    "$ROOT/data/anduinos-btrfs-snapshots-manager-notifier.service"
grep -Fq 'Restart=on-failure' "$ROOT/data/anduinos-btrfs-snapshots-manager-notifier.service"
grep -Fq 'ensure_notifier_running' "$ROOT/src/btrfs-snapshots-manager/src/application.rs"
if rg -n 'path[[:space:]]*=[[:space:]]*"src/bin/' \
    "$ROOT/src" --glob 'Cargo.toml'; then
    echo "Executable Rust sources must use the repository-standard src/*.rs layout" >&2
    exit 1
fi
test -f "$ROOT/data/90-anduinos-btrfs-snapshots-manager"
test -f "$ROOT/assets/apt-snapshots.toml"
grep -Fq 'snapshot_before = true' "$ROOT/assets/apt-snapshots.toml"
grep -Fq 'snapshot_after = false' "$ROOT/assets/apt-snapshots.toml"
rg -q 'get_apt_snapshot_policy' "$ROOT/src/btrfs-snapshots-manager-helper/src/main.rs"
rg -q 'save_apt_snapshot_policy' "$ROOT/src/btrfs-snapshots-manager-helper/src/main.rs"
rg -q 'Create a system snapshot before changes' "$ROOT/src/btrfs-snapshots-manager/src/ui/advanced_settings.rs"
rg -q 'ViewStack::new' "$ROOT/src/btrfs-snapshots-manager/src/ui/mod.rs"
rg -q 'SnapshotPage::new.*SnapshotScope::System' "$ROOT/src/btrfs-snapshots-manager/src/ui/mod.rs"
rg -q 'SnapshotPage::new.*SnapshotScope::Home' "$ROOT/src/btrfs-snapshots-manager/src/ui/mod.rs"
rg -q 'evaluate_retention' "$ROOT/src/btrfs-snapshots-manager-helper/src/main.rs"
rg -q 'ListSystemSnapshotFiles' "$ROOT/src/btrfs-snapshots-manager/src/dbus_client.rs"
! rg -qi 'external.?backup|backup-(destinations|export|import|delete)|CompareSnapshots' "$ROOT/src/btrfs-snapshots-manager-cli"
grep -Fq 'if [ -x /usr/libexec/anduinos-btrfs-snapshots-manager-apt-hook ]' \
    "$ROOT/data/90-anduinos-btrfs-snapshots-manager"
test -f "$ROOT/scripts/postrm.sh"
test -f "$ROOT/docs/deployment-v1.schema.json"
test -f "$ROOT/docs/rollback-v1.schema.json"
test -f "$ROOT/docs/personal-snapshot-v1.schema.json"
test -f "$ROOT/docs/VM-QUALIFICATION.md"
test -f "$ROOT/docs/RECOVERY-SCOPE.md"
test -x "$ROOT/scripts/test-recovery-operations-loopback.sh"
test -x "$ROOT/scripts/qualify-recovery-vm.sh"
test -x "$ROOT/scripts/test-installed-policy.sh"
test -x "$ROOT/scripts/check-i18n.py"
test -x "$ROOT/scripts/update-i18n.py"
test -x "$ROOT/scripts/test-gui-smoke.sh"
test -x "$ROOT/scripts/screenshot-demo-service.py"
python3 - "$ROOT/scripts/screenshot-demo-service.py" <<'PY'
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    compile(source.read(), sys.argv[1], "exec")
PY
python3 - "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py" <<'PY'
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    compile(source.read(), sys.argv[1], "exec")
PY
grep -Fq '<Dependency Include="python3-nautilus"' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'Target="/usr/share/nautilus-python/extensions/anduinos_btrfs_snapshots_manager_file_history.py"' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'Target="/usr/share/dbus-1/services/org.anduinos.BtrfsSnapshotsManager.service"' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'Gio.BusType.SESSION' "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py"
grep -Fq 'def get_file_items' "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py"
grep -Fq 'def get_background_items' "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py"
grep -Fq 'View File History…' "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py"
grep -Fq 'Browse This Folder’s History…' "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py"
grep -Fq 'SimpleAction::new("file-history"' "$ROOT/src/btrfs-snapshots-manager/src/application.rs"
grep -Fq 'Exec=/usr/bin/anduinos-btrfs-snapshots-manager --gapplication-service' \
    "$ROOT/data/org.anduinos.BtrfsSnapshotsManager.Session.service"
if rg -n 'BusType\.SYSTEM|subprocess|os\.system|Popen|anduinos-btrfs-snapshots-manager-helper' \
    "$ROOT/data/anduinos_btrfs_snapshots_manager_file_history.py"; then
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

rg -q 'rm -f -- /etc/anduinos-btrfs-snapshots-manager/apt-snapshots.toml' "$ROOT/scripts/postrm.sh"
rg -q 'rm -f -- /etc/anduinos-btrfs-snapshots-manager/automation.toml' "$ROOT/scripts/postrm.sh"
rg -q 'systemctl enable anduinos-btrfs-snapshots-manager-confirm.service' "$ROOT/scripts/postinst.sh"
rg -q 'systemctl disable --now anduinos-btrfs-snapshots-manager-confirm.service' "$ROOT/scripts/prerm.sh"
grep -Fq 'anduinos-btrfs-snapshots-manager-confirm.service" AutoEnable="false"' \
    "$ROOT/anduinos-btrfs-snapshots-manager.aosproj"
if rg -n 'rm -r[f ]|find .*RECOVERY_STORE|/\.snapshots/anduinos-btrfs-snapshots-manager' "$ROOT/scripts/postrm.sh"; then
    echo "Package removal must never recursively delete snapshot data" >&2
    exit 1
fi

python3 "$ROOT/scripts/check-i18n.py"

rg -q 'AutomaticScope::System' "$ROOT/src/btrfs-snapshots-manager-scheduler/src/main.rs"
rg -q 'AutomaticScope::Home' "$ROOT/src/btrfs-snapshots-manager-scheduler/src/main.rs"
rg -q 'create-scheduled\) cmd_create_scheduled' "$ROOT/src/btrfs-snapshots-manager-cli"
rg -q 'CreateScheduledDeployment' "$ROOT/src/btrfs-snapshots-manager-cli"
rg -q 'CreateScheduledPersonalSnapshot' "$ROOT/src/btrfs-snapshots-manager-cli"
rg -q 'notify_after_success' "$ROOT/src/snapshots-manager-common/src/automation.rs"
rg -q 'SnapshotCreationSucceeded' "$ROOT/src/btrfs-snapshots-manager-notifier/src/main.rs"
rg -q 'AutomaticCleanupSucceeded' "$ROOT/src/btrfs-snapshots-manager-notifier/src/main.rs"
if rg -n 'notify-send|org\.freedesktop\.Notifications' \
    "$ROOT/src/btrfs-snapshots-manager-helper/src" "$ROOT/src/btrfs-snapshots-manager-scheduler/src"; then
    echo "Privileged services must not send desktop-session notifications directly" >&2
    exit 1
fi
if rg -n 'RECOVERY_STORE_ROOT|/\.snapshots|ListPersonalFiles|ExportPersonalFile' \
    "$ROOT/src/btrfs-snapshots-manager-notifier/src"; then
    echo "The desktop notifier must not gain recovery-store or file-browsing capabilities" >&2
    exit 1
fi
rg -Fq ".data[0] | booleans | tostring" "$ROOT/src/btrfs-snapshots-manager-cli"
rg -Fq 'status) cmd_status' "$ROOT/src/btrfs-snapshots-manager-cli"
rg -Fq 'create [--json]' "$ROOT/src/btrfs-snapshots-manager-cli"

if rg -n 'xbps|sudo sv|/var/service|/etc/sv|/etc/btrfs-snapshots-manager|\.config/btrfs-snapshots-manager|\.local/share/btrfs-snapshots-manager|/var/lib/btrfs-snapshots-manager|tech\.geektoshi\.btrfs-snapshots-manager|com\.voidlinux\.btrfs-snapshots-manager|from_icon_name\("btrfs-snapshots-manager"|set-default|get-default|root-writable|cleanup-writable-snapshots|System rollback is disabled in this development build' \
    "$ROOT/src" --glob '!Cargo.lock'; then
    echo "Void/upstream platform bindings remain in the buildable source" >&2
    exit 1
fi

if rg -n 'BackupSnapshot|RestoreFromBackup|ScanBackupDestinations|ApplyBackupRetention|destination_mount|backup_path|snapshot_path_from_name|RestoreFiles|restore_files|ListSnapshots|list_snapshots|CleanupSnapshots|cleanup_snapshots' \
    "$ROOT/src" --glob '!Cargo.lock' --glob '!target/**'; then
    echo "A removed caller-path privileged ABI remains in buildable source" >&2
    exit 1
fi

if rg -n 'affected_subvolumes|personal_files_affected|SnapshotInfo|SnapshotTarget|pub mod targets' \
    "$ROOT/src" --glob '!Cargo.lock' --glob '!target/**'; then
    echo "A removed generic/custom recovery-scope model remains in buildable source" >&2
    exit 1
fi

if rg -n 'SnapshotAction::Browse|open_containing_folder' \
    "$ROOT/src" --glob '!target/**'; then
    echo "The root-private recovery store must not be exposed as a desktop browse path" >&2
    exit 1
fi

if rg -n 'Command::new\("(?:stat|df|btrfs)"\)|/\.snapshots/anduinos-btrfs-snapshots-manager|\bmod cache\b|\bTtlCache\b' \
    "$ROOT/src/btrfs-snapshots-manager/src" --glob '*.rs'; then
    echo "The desktop UI must not duplicate privileged storage probes or model root-private paths" >&2
    exit 1
fi

if rg -n 'inspect_anduinos_layout' "$ROOT/src/btrfs-snapshots-manager/src" --glob '*.rs'; then
    echo "The desktop UI must use the helper-owned layout report, not a local layout probe" >&2
    exit 1
fi

if rg -n '/tmp/anduinos-btrfs-snapshots-manager.*preferences' "$ROOT/src/btrfs-snapshots-manager/src" --glob '*.rs'; then
    echo "Per-user preferences must never fall back to a shared predictable /tmp path" >&2
    exit 1
fi

! rg -q 'CompareSnapshots|CompareDeploymentPackages|ExternalBackup' \
    "$ROOT/src/btrfs-snapshots-manager/src" "$ROOT/src/btrfs-snapshots-manager-helper/src" \
    "$ROOT/src/anduinos-recovery-engine/src"
rg -q 'ApplyScheduleRetention' "$ROOT/src/btrfs-snapshots-manager-cli"
rg -q 'ExportPersonalFile' "$ROOT/src/btrfs-snapshots-manager/src/dbus_client.rs"

if rg -n 'Command::new\("(?:/usr/bin/)?(?:apt|apt-get|aptitude|pkcon)"|run_command\("(?:/usr/bin/)?(?:apt|apt-get|aptitude|pkcon)"' \
    "$ROOT/src" --glob '!target/**'; then
    echo "Arbitrary package installation or package-manager execution entered Disk Snapshots Manager" >&2
    exit 1
fi
