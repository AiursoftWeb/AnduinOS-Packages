#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"

ARCH=$1
if [ -z "$ARCH" ]; then
    ARCH="amd64"
fi

echo "Building deskmon for architecture: $ARCH"

mkdir -p obj/usr/bin obj/usr/lib/systemd/user

if [ "$ARCH" == "arm64" ]; then
    need_cmd aarch64-linux-gnu-gcc
    need_cmd aarch64-linux-gnu-pkg-config
    aarch64-linux-gnu-gcc -O2 $(aarch64-linux-gnu-pkg-config --cflags glib-2.0 gio-2.0) src/deskmon.c -o obj/usr/bin/deskmon $(aarch64-linux-gnu-pkg-config --libs glib-2.0 gio-2.0)
else
    need_cmd gcc
    need_cmd pkg-config
    gcc -O2 $(pkg-config --cflags glib-2.0 gio-2.0) src/deskmon.c -o obj/usr/bin/deskmon $(pkg-config --libs glib-2.0 gio-2.0)
fi
