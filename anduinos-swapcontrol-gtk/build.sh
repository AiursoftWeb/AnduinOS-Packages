#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"

ARCH=$1
if [ -z "$ARCH" ]; then
    ARCH="amd64"
fi

need_cmd python3

echo "Compiling locales..."
bash "$SCRIPT_DIR/compile-locales.sh"

echo "Building swapcontrol-gtk for architecture: $ARCH"

mkdir -p obj

if [ "$ARCH" == "arm64" ]; then
    need_cmd cargo
    need_cmd aarch64-linux-gnu-gcc

    # Set up pkg-config for cross-compiling GTK4 and Libadwaita
    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig

    # Tell Cargo to use the aarch64 GCC linker
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc

    cargo build --locked --release --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/swapcontrol-gtk obj/swapcontrol-gtk
else
    need_cmd cargo

    cargo build --locked --release
    cp target/release/swapcontrol-gtk obj/swapcontrol-gtk
fi

# Keep upstream crate and Rust standard-library attribution with the executable.
if [ "$ARCH" == "arm64" ]; then
    NOTICE_TARGET=aarch64-unknown-linux-gnu
else
    NOTICE_TARGET="$(rustc -vV | sed -n 's/^host: //p')"
fi
python3 "$SCRIPT_DIR/scripts/rust-notices.py" "$NOTICE_TARGET" obj/RUST-THIRD-PARTY-NOTICES
