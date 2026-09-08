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

install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-helper" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-helper"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-scheduler" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-scheduler"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-notifier" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-notifier"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-initramfs" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-initramfs"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-boot-config" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-boot-config"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-confirm" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-confirm"
install -m755 "$RELEASE_DIR/anduinos-btrfs-snapshots-manager-apt-hook" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-apt-hook"
install -m755 "$SCRIPT_DIR/src/btrfs-snapshots-manager-cli" "$SCRIPT_DIR/obj/anduinos-btrfs-snapshots-manager-cli"
