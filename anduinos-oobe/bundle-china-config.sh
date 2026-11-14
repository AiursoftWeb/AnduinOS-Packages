#!/usr/bin/env bash
set -euo pipefail
umask 022
cd "$(dirname "$0")"
suite="$1"
case "$suite" in
    noble-addon|resolute-addon|stonking-addon) ;;
    *) echo 'Unsupported OOBE target' >&2; exit 1 ;;
esac
for arch in amd64 arm64; do
    output="obj/china-config/$suite/$arch"
    mkdir -p "$output"
    apkg build --path ../anduinos-apt-config-for-china --distro anduinos \
        --suite "$suite" --arch "$arch" --output "$PWD/$output/build"
    packages=("$output"/build/*.deb)
    [[ ${#packages[@]} == 1 && -f "${packages[0]}" ]]
    cp "${packages[0]}" "$output/china-apt-config.deb"
done
