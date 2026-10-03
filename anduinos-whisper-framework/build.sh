#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
arch="${1:-$(dpkg --print-architecture)}"
case "$arch" in
    amd64) target=x86_64-unknown-linux-gnu; multiarch=x86_64-linux-gnu ;;
    arm64) target=aarch64-unknown-linux-gnu; multiarch=aarch64-linux-gnu ;;
    *) echo 'Expected amd64 or arm64' >&2; exit 2 ;;
esac
command -v cargo >/dev/null
command -v pkg-config >/dev/null
if [[ "$arch" != "$(dpkg --print-architecture)" ]]; then
    command -v "$multiarch-gcc" >/dev/null
    export PKG_CONFIG_ALLOW_CROSS=1
    export PKG_CONFIG_LIBDIR="/usr/lib/$multiarch/pkgconfig:/usr/share/pkgconfig"
    # Do not accidentally consume host .pc files in cross builds. A dedicated
    # target sysroot may be supplied explicitly by the build environment.
    export PKG_CONFIG_PATH="${VOICE_CROSS_PKG_CONFIG_PATH:-}"
    case "$arch" in
        arm64) export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc ;;
        amd64) export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc ;;
    esac
fi
pkg-config --exists gio-2.0 gstreamer-1.0 gstreamer-app-1.0
cargo build --locked --release --target "$target" --bins
python3 scripts/rust-notices.py "$target" "obj/$arch/RUST-THIRD-PARTY-NOTICES"
output="${CARGO_TARGET_DIR:-target}/$target/release"
for binary in anduinos-whisper-framework anduinos-voice-diagnostics; do
    install -Dm755 "$output/$binary" "obj/$arch/$binary"
done
