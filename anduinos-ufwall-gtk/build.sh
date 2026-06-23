#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"

ARCH=$1
if [ -z "$ARCH" ]; then
    ARCH="amd64"
fi

echo "Compiling locales..."
bash "$SCRIPT_DIR/compile-locales.sh"

echo "Building ufwall-gtk for architecture: $ARCH"

mkdir -p obj

if [ "$ARCH" == "arm64" ]; then
    need_cmd cargo
    need_cmd aarch64-linux-gnu-gcc

    # Set up pkg-config for cross-compiling GTK4 and Libadwaita
    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig

    # Tell Cargo to use the aarch64 GCC linker
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc

    cargo build --release --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/ufwall-gtk obj/ufwall-gtk
else
    need_cmd cargo

    cargo build --release
    cp target/release/ufwall-gtk obj/ufwall-gtk
fi
