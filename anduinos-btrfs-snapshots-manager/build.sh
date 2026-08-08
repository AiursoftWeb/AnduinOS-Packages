#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
ARCH="${1:-amd64}"
MANIFEST="$SCRIPT_DIR/src/Cargo.toml"
# Release LTO requires a deeper rustc worker stack than the platform default on
# some supported builders. Keep this deterministic inside APKG instead of
# relying on an interactive shell's environment.
export RUST_MIN_STACK="${RUST_MIN_STACK:-33554432}"

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

install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-helper" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper"
if rg -a -n 'ScanBackupDestinations|BackupSnapshot|RestoreFromBackup|destination_mount|backup_path|RestoreFiles|ListSnapshots' \
    "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager"; then
    echo "A removed caller-path privileged ABI leaked into a release binary" >&2
    exit 1
fi
for method in GetPrivilegedRecoveryEngineStatus ApplyScheduleRetention BeginSystemSnapshotBrowse EndSystemSnapshotBrowse ListSystemSnapshotFiles ExportSystemSnapshotFile DeleteDeployments DeletePersonalSnapshots ReconcileDeploymentRestore; do
    if ! rg -a -q "<method name=\"$method\">" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper"; then
        echo "Required Disk Snapshots Manager 2.0 D-Bus method is missing: $method" >&2
        exit 1
    fi
done
for method in GetAptSnapshotPolicy SaveAptSnapshotPolicy GetBtrfsFilesystemStatus RunBtrfsMaintenanceAction; do
    if ! rg -a -q "<method name=\"$method\">" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper"; then
        echo "Required APT policy D-Bus method is missing: $method" >&2
        exit 1
    fi
done
for method in CreatePersonalSnapshot CreateScheduledPersonalSnapshot ListPersonalFiles ExportPersonalFile; do
    if ! rg -a -q "<method name=\"$method\">" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper"; then
        echo "Required Personal Files D-Bus method is missing: $method" >&2
        exit 1
    fi
done
for signal in SnapshotCreationSucceeded AutomaticSnapshotStarting AutomaticSnapshotFailed AutomaticCleanupSucceeded; do
    if ! rg -a -q "<signal name=\"$signal\">" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper"; then
        echo "Required automatic notification D-Bus signal is missing: $signal" >&2
        exit 1
    fi
done
if rg -a -q '<method name="\(CleanupSnapshots\|CompareSnapshots\|CompareDeploymentPackages\|ListBackupDestinations\|ExportDeployment\|ImportExternalBackup\|SaveSchedulesConfig\)">' "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper"; then
    echo "A removed Disk Snapshots Manager 1.x method leaked into the release binary" >&2
    exit 1
fi
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-scheduler" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-scheduler"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-notifier" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-notifier"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-initramfs" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-initramfs"
bash "$SCRIPT_DIR/scripts/test-recovery-artifacts.sh" \
    "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-initramfs"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-boot-config" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-boot-config"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-confirm" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-confirm"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-apt-hook" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-apt-hook"
install -m755 "$SCRIPT_DIR/src/btrfs-snapshots-manager-cli" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-cli"
