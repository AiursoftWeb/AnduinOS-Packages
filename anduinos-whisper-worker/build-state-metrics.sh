#!/bin/bash
# Private whisper.cpp library; distro GGML remains a separately updated backend.
set -euo pipefail
cd "$(dirname "$0")"
architecture="${1:?Expected amd64 or arm64}"
mkdir -p obj/state-metrics obj/downloads "obj/$architecture/anduinos-whisper"
fetch() {
    local relative="$1" digest="$2" destination="obj/state-metrics/${1##*/}"
    if ! test -f "$destination" || ! echo "$digest  $destination" | sha256sum --check --status 2>/dev/null; then
        curl --fail --location --retry 2 --connect-timeout 15 --max-time 90 \
            "https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.8.3/$relative" -o "$destination.part"
        echo "$digest  $destination.part" | sha256sum --check --status
        mv "$destination.part" "$destination"
    fi
}
fetch src/whisper.cpp d1e041704c12e20b8eb9596632db3b3b31046348dbb0e493f8a5ab8edd5f4376
fetch src/whisper-arch.h f35cf51d54c4df7e1c504ba8ef3f06012ba483459ddf2a5cba533deb7b85c239
fetch LICENSE e562a2ddfaf8280537795ac5ecd34e3012b6582a147ef69ba6a6a5c08c84757d

fetch_deb() {
    local relative="$1" digest="$2" destination="obj/downloads/${1##*/}"
    if ! test -f "$destination" || ! echo "$digest  $destination" | sha256sum --check --status 2>/dev/null; then
        curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
            "https://mirror.aiursoft.com/ubuntu/$relative" -o "$destination.part"
        echo "$digest  $destination.part" | sha256sum --check --status
        mv "$destination.part" "$destination"
    fi
    dpkg-deb --extract "$destination" "$toolchain"
}
# Existing GCC drivers/binutils provide the base toolchain. Matching C++ frontend
# and headers are extracted locally: builds never apt-install on the host.
test "$(uname -m)" = x86_64 || { echo 'This cross-build recipe requires an amd64 build host' >&2; exit 1; }
toolchain="$PWD/obj/cxx-$architecture"
case "$architecture" in
    amd64)
        driver=gcc
        triple=x86_64-linux-gnu
        fetch_deb pool/main/g/gcc-15/g++-15-x86-64-linux-gnu_15.2.0-16ubuntu1_amd64.deb \
            8673a23519f570b4a5b4a663a9ce3b0e2f0be50c7250ce7fdae19eb064c3d6aa
        fetch_deb pool/main/g/gcc-15/libstdc++-15-dev_15.2.0-16ubuntu1_amd64.deb \
            9a400597b1db45cf51238c9eb556e211ec092d363cfb7ebebac627750ba56433
        fetch_deb pool/universe/g/ggml/libggml0_0.9.11-1_amd64.deb \
            871c666330f0597aff3a23eb60e1f29cb8aa6ea70c35f0683f30c7405cf82c9f
        frontend="$toolchain/usr/libexec/gcc/$triple/15/"
        includes=("$toolchain/usr/include/c++/15" "$toolchain/usr/include/$triple/c++/15" "$toolchain/usr/include/c++/15/backward")
        ;;
    arm64)
        driver=aarch64-linux-gnu-gcc
        triple=aarch64-linux-gnu
        fetch_deb pool/main/g/gcc-15-cross/g++-15-aarch64-linux-gnu_15.2.0-16ubuntu1cross1_amd64.deb \
            988221ff508e6150282f535b404ebeeb2601875daf1acf5814bc66d3ff66e10e
        fetch_deb pool/main/g/gcc-15-cross/libstdc++-15-dev-arm64-cross_15.2.0-16ubuntu1cross1_all.deb \
            a88bc813f2bb86ca21b57018c405510bd3cc3d93202ae16d44561b619e16d66d
        fetch_deb pool/universe/g/ggml/libggml0_0.9.11-1_arm64.deb \
            55dbd75060b34c8926f3e5e1d52351ad6ec295cab0a30f269748a664609b13e8
        frontend="$toolchain/usr/libexec/gcc-cross/$triple/15/"
        includes=("$toolchain/usr/$triple/include/c++/15" "$toolchain/usr/$triple/include/c++/15/$triple" "$toolchain/usr/$triple/include/c++/15/backward")
        ;;
    *) echo 'Expected amd64 or arm64' >&2; exit 2 ;;
esac
test "$("$driver" -dumpversion)" = 15 || { echo 'GCC 15 driver required for the pinned C++ frontend' >&2; exit 1; }
compiler=("$driver" -x c++ -B "$frontend")
for include in "${includes[@]}"; do compiler+=(-isystem "$include"); done
"${compiler[@]}" -std=c++11 -O2 -fPIC -shared -pthread \
    -fstack-protector-strong -D_FORTIFY_SOURCE=2 \
    -DWHISPER_VERSION='"1.8.3"' -DWHISPER_SHARED -DWHISPER_BUILD \
    -I obj/state-metrics -I obj/headers/usr/include src/state-metrics.cpp \
    -L "$toolchain/usr/lib/$triple" -Wl,-z,defs,-z,relro,-z,now,-soname,libanduinos-whisper.so.1 \
    -l:libggml.so.0 -l:libggml-base.so.0 -l:libstdc++.so.6 -lm \
    -o "obj/$architecture/anduinos-whisper/libanduinos-whisper.so.1"
