#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/build-guards.sh
source "$SCRIPT_DIR/../lib/build-guards.sh"

ARCH="${1:-amd64}"

echo "Compiling locales..."
bash "$SCRIPT_DIR/compile-locales.sh"

echo "Building libnautilus-copy-file-path.so for architecture: $ARCH"

OUT_DIR="$SCRIPT_DIR/obj/$ARCH"
mkdir -p "$OUT_DIR"

COMMON_CFLAGS=(
    -shared
    -fPIC
    -fvisibility=hidden
    -O2
    -Wall
    -Wextra
    -Werror
    -DGETTEXT_PACKAGE='"anduinos-copy-file-path"'
    -DLOCALEDIR='"/usr/share/locale"'
)

SOURCES=(
    "$SCRIPT_DIR/src/copy_file_path_core.c"
    "$SCRIPT_DIR/src/copy_file_path_extension.c"
)

if [ "$ARCH" = "arm64" ]; then
    need_cmd aarch64-linux-gnu-gcc gcc-aarch64-linux-gnu
    need_cmd pkg-config pkg-config
    PKG_CFLAGS="$(PKG_CONFIG_PATH='' PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig pkg-config --cflags libnautilus-extension-4 gtk4)"
    PKG_LIBS="$(PKG_CONFIG_PATH='' PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig pkg-config --libs libnautilus-extension-4 gtk4)"
    # shellcheck disable=SC2086
    aarch64-linux-gnu-gcc \
        "${COMMON_CFLAGS[@]}" \
        $PKG_CFLAGS \
        "${SOURCES[@]}" \
        -o "$OUT_DIR/libnautilus-copy-file-path.so" \
        -Wl,--as-needed \
        $PKG_LIBS
elif [ "$ARCH" = "amd64" ]; then
    need_cmd gcc build-essential
    need_cmd pkg-config pkg-config
    PKG_CFLAGS="$(pkg-config --cflags libnautilus-extension-4 gtk4)"
    PKG_LIBS="$(pkg-config --libs libnautilus-extension-4 gtk4)"
    # shellcheck disable=SC2086
    gcc \
        "${COMMON_CFLAGS[@]}" \
        $PKG_CFLAGS \
        "${SOURCES[@]}" \
        -o "$OUT_DIR/libnautilus-copy-file-path.so" \
        -Wl,--as-needed \
        $PKG_LIBS
else
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
fi
