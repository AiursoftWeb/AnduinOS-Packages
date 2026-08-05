#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
ARCH="${1:-amd64}"
MANIFEST="$SCRIPT_DIR/src/Cargo.toml"

need_cmd cargo
need_cmd msgfmt gettext
mkdir -p "$SCRIPT_DIR/obj"
bash "$SCRIPT_DIR/compile-locales.sh"

if [ "$ARCH" = "arm64" ]; then
    need_cmd aarch64-linux-gnu-gcc gcc-aarch64-linux-gnu
    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
    cargo build --manifest-path "$MANIFEST" --workspace --release --locked \
        --target aarch64-unknown-linux-gnu
    RELEASE_DIR="$SCRIPT_DIR/src/target/aarch64-unknown-linux-gnu/release"
else
    cargo build --manifest-path "$MANIFEST" --workspace --release --locked
    RELEASE_DIR="$SCRIPT_DIR/src/target/release"
fi

install -m755 "$RELEASE_DIR/anduinos-waypoint-gtk" "$SCRIPT_DIR/obj/anduinos-waypoint-gtk"
install -m755 "$RELEASE_DIR/anduinos-waypoint-helper" "$SCRIPT_DIR/obj/anduinos-waypoint-helper"
if rg -a -n 'ScanBackupDestinations|BackupSnapshot|RestoreFromBackup|destination_mount|backup_path|RestoreFiles|ListSnapshots' \
    "$SCRIPT_DIR/obj/anduinos-waypoint-helper" "$SCRIPT_DIR/obj/anduinos-waypoint-gtk"; then
    echo "A removed caller-path privileged ABI leaked into a release binary" >&2
    exit 1
fi
for method in ListBackupDestinations ExportDeployment ImportExternalBackup DeleteExternalBackup CompareDeploymentPackages ApplyScheduleRetention; do
    if ! rg -a -q "<method name=\"$method\">" "$SCRIPT_DIR/obj/anduinos-waypoint-helper"; then
        echo "Required UUID-based backup D-Bus method is missing: $method" >&2
        exit 1
    fi
done
for method in CreatePersonalSnapshot CreateScheduledPersonalSnapshot ListPersonalFiles ExportPersonalFile ExportPersonalSnapshot ImportPersonalExternalBackup; do
    if ! rg -a -q "<method name=\"$method\">" "$SCRIPT_DIR/obj/anduinos-waypoint-helper"; then
        echo "Required Personal Files D-Bus method is missing: $method" >&2
        exit 1
    fi
done
for signal in AutomaticSnapshotCreated AutomaticSnapshotsDeleted; do
    if ! rg -a -q "<signal name=\"$signal\">" "$SCRIPT_DIR/obj/anduinos-waypoint-helper"; then
        echo "Required automatic notification D-Bus signal is missing: $signal" >&2
        exit 1
    fi
done
if rg -a -q '<method name="CleanupSnapshots">' "$SCRIPT_DIR/obj/anduinos-waypoint-helper"; then
    echo "The obsolete generic CleanupSnapshots D-Bus method leaked into the release binary" >&2
    exit 1
fi
install -m755 "$RELEASE_DIR/anduinos-waypoint-scheduler" "$SCRIPT_DIR/obj/anduinos-waypoint-scheduler"
install -m755 "$RELEASE_DIR/anduinos-waypoint-notifier" "$SCRIPT_DIR/obj/anduinos-waypoint-notifier"
install -m755 "$RELEASE_DIR/anduinos-waypoint-initramfs" "$SCRIPT_DIR/obj/anduinos-waypoint-initramfs"
install -m755 "$RELEASE_DIR/anduinos-waypoint-boot-config" "$SCRIPT_DIR/obj/anduinos-waypoint-boot-config"
install -m755 "$RELEASE_DIR/anduinos-waypoint-confirm" "$SCRIPT_DIR/obj/anduinos-waypoint-confirm"
install -m755 "$RELEASE_DIR/anduinos-waypoint-apt-hook" "$SCRIPT_DIR/obj/anduinos-waypoint-apt-hook"
install -m755 "$SCRIPT_DIR/src/waypoint-cli" "$SCRIPT_DIR/obj/anduinos-waypoint-cli"
