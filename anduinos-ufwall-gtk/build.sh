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

echo "Building ufwall-gtk for architecture: $ARCH"

# Build-time dependencies (Cargo crates link against these system libs)
if ! pkg-config --exists libpcap; then
    echo "BUILD ERROR: libpcap-dev is required. Install with: sudo apt install -y libpcap-dev" >&2
    exit 1
fi

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
    cp target/aarch64-unknown-linux-gnu/release/ufwall-gtk obj/ufwall-gtk
    cp target/aarch64-unknown-linux-gnu/release/ufwall-auditor obj/ufwall-auditor
else
    need_cmd cargo

    cargo build --locked --release
    cp target/release/ufwall-gtk obj/ufwall-gtk
    cp target/release/ufwall-auditor obj/ufwall-auditor
fi

# Keep upstream crate and Rust standard-library attribution with the executable.
if [ "$ARCH" == "arm64" ]; then
    NOTICE_TARGET=aarch64-unknown-linux-gnu
else
    NOTICE_TARGET="$(rustc -vV | sed -n 's/^host: //p')"
fi
python3 "$SCRIPT_DIR/scripts/rust-notices.py" "$NOTICE_TARGET" obj/RUST-THIRD-PARTY-NOTICES.gz
