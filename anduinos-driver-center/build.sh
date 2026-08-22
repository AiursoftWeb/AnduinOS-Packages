#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"

ARCH=$1
if [ -z "$ARCH" ]; then
    ARCH="amd64"
fi

SYS_PC="/usr/lib/${DEB_HOST_MULTIARCH:-x86_64-linux-gnu}/pkgconfig"
if [ "$ARCH" = "arm64" ]; then
    SYS_PC="/usr/lib/aarch64-linux-gnu/pkgconfig"
fi
LOCAL_PC="${HOME}/.local/opt/gtk-dev/usr/lib/x86_64-linux-gnu/pkgconfig"
if [ -f "$SYS_PC/gtk4.pc" ]; then
    export PKG_CONFIG_PATH="$SYS_PC"
elif [ -f "$LOCAL_PC/gtk4.pc" ]; then
    export PKG_CONFIG_PATH="$LOCAL_PC:$SYS_PC"
fi

echo "Compiling locales..."
bash "$SCRIPT_DIR/compile-locales.sh"

echo "Building anduinos-driver-center for architecture: $ARCH"
mkdir -p obj

if [ "$ARCH" = "arm64" ]; then
    need_cmd cargo
    need_cmd aarch64-linux-gnu-gcc
    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
    cargo test --locked
    cargo build --release --locked --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/anduinos-driver-center obj/anduinos-driver-center
else
    need_cmd cargo
    cargo test
    cargo build --release
    cp target/release/anduinos-driver-center obj/anduinos-driver-center
fi
