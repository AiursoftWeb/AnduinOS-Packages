#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
ARCH="${1:-amd64}"

need_cmd python3
need_cmd msgfmt
bash "$SCRIPT_DIR/compile-locales.sh"

mkdir -p obj
if [ "$ARCH" = "arm64" ]; then
    need_cmd cargo
    need_cmd aarch64-linux-gnu-gcc
    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
    cargo build --locked --release --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/anduinos-yubikey-manager obj/
else
    need_cmd cargo
    cargo build --locked --release
    cp target/release/anduinos-yubikey-manager obj/
fi

# Keep attribution beside the binary, including the statically linked Rust stdlib.
if [ "$ARCH" = "arm64" ]; then
    NOTICE_TARGET=aarch64-unknown-linux-gnu
else
    NOTICE_TARGET="$(rustc -vV | sed -n 's/^host: //p')"
fi
python3 "$SCRIPT_DIR/scripts/rust-notices.py" "$NOTICE_TARGET" obj/RUST-THIRD-PARTY-NOTICES
