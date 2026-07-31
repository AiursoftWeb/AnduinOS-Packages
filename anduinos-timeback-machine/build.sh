#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
ARCH="${1:-amd64}"

need_cmd cargo
mkdir -p "$SCRIPT_DIR/obj"

if [ "$ARCH" = "arm64" ]; then
    need_cmd aarch64-linux-gnu-gcc gcc-aarch64-linux-gnu
    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
    cargo build --release --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/anduinos-timeback-machine obj/
    cp target/aarch64-unknown-linux-gnu/release/anduinos-timebackd obj/
    cp target/aarch64-unknown-linux-gnu/release/timebackctl obj/
else
    cargo build --release
    cp target/release/anduinos-timeback-machine obj/
    cp target/release/anduinos-timebackd obj/
    cp target/release/timebackctl obj/
fi
