#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"

ARCH=$1
if [ -z "$ARCH" ]; then
    ARCH="amd64"
fi

echo "Building anduinos-theme-schedule for architecture: $ARCH"
mkdir -p obj

if [ "$ARCH" = "arm64" ]; then
    need_cmd cargo
    need_cmd aarch64-linux-gnu-gcc
    export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
    cargo test --locked
    cargo build --release --locked --target aarch64-unknown-linux-gnu
    cp target/aarch64-unknown-linux-gnu/release/anduinos-theme-schedule obj/anduinos-theme-schedule
else
    need_cmd cargo
    cargo test
    cargo build --release
    cp target/release/anduinos-theme-schedule obj/anduinos-theme-schedule
fi
