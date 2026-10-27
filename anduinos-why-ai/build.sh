#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd python3

ARCH=$1
if [ -z "$ARCH" ]; then
    ARCH="amd64"
fi

echo "Building why (anduinos-why-ai) for architecture: $ARCH"

mkdir -p obj

if [ "$ARCH" == "arm64" ]; then
    target=aarch64-unknown-linux-gnu
    need_cmd cargo
    need_cmd aarch64-linux-gnu-gcc

    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc

    cargo build --locked --release --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/why obj/why
else
    target=x86_64-unknown-linux-gnu
    need_cmd cargo

    cargo build --locked --release
    cp target/release/why obj/why
fi

python3 "$SCRIPT_DIR/../anduinos-whisper-framework/scripts/rust-notices.py" --package encoding_rs \
    "$target" "$SCRIPT_DIR/obj/ENCODING-NOTICES"
