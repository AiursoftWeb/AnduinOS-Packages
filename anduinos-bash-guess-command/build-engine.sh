#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
ARCH=${1:-amd64}
need_cmd cargo

case $ARCH in
    amd64)
        target=x86_64-unknown-linux-gnu
        ;;
    arm64)
        target=aarch64-unknown-linux-gnu
        need_cmd aarch64-linux-gnu-gcc gcc-aarch64-linux-gnu
        export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
        ;;
    *)
        printf 'build-engine.sh: unsupported architecture: %s\n' "$ARCH" >&2
        exit 2
        ;;
esac

cargo build --offline --release --manifest-path "$SCRIPT_DIR/engine/Cargo.toml" \
    --target "$target" --bin anduinos-quietd
install -D -m 0755 \
    "$SCRIPT_DIR/engine/target/$target/release/anduinos-quietd" \
    "$SCRIPT_DIR/deploy/$ARCH/anduinos-quietd"
