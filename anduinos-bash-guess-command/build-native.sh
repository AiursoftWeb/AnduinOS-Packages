#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
ARCH=${1:-amd64}

case $ARCH in
    amd64)
        compiler=gcc
        strip_tool=strip
        ;;
    arm64)
        compiler=aarch64-linux-gnu-gcc
        strip_tool=aarch64-linux-gnu-strip
        need_cmd "$compiler" gcc-aarch64-linux-gnu
        ;;
    *)
        printf 'build-native.sh: unsupported architecture: %s\n' "$ARCH" >&2
        exit 2
        ;;
esac

need_cmd "$compiler"
need_cmd "$strip_tool"

"$compiler" -std=c11 -O2 -fPIC -fstack-protector-strong \
    -Wall -Wextra -Werror -I"$SCRIPT_DIR/native" \
    -shared -Wl,-z,relro,-z,now \
    -o "$SCRIPT_DIR/deploy/$ARCH/anduinos-ghost.so" \
    "$SCRIPT_DIR/native/anduinos_ghost.c"
"$strip_tool" --strip-unneeded "$SCRIPT_DIR/deploy/$ARCH/anduinos-ghost.so"
