#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"

ARCH=$1
if [ -z "$ARCH" ]; then
    ARCH="amd64"
fi

echo "Building why (anduinos-why-ai) for architecture: $ARCH"

mkdir -p obj

if [ "$ARCH" == "arm64" ]; then
    need_cmd cargo
    need_cmd aarch64-linux-gnu-gcc

    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc

    cargo build --release --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/why obj/why
else
    need_cmd cargo

    cargo build --release
    cp target/release/why obj/why
fi
