#!/usr/bin/env bash
set -euo pipefail
umask 022
cd "$(dirname "$0")"
suite="$1"
arch="$2"
case "$suite/$arch" in
    resolute-addon/amd64|resolute-addon/arm64|stonking-addon/amd64|stonking-addon/arm64) ;;
    *) echo 'Unsupported installer target' >&2; exit 1 ;;
esac
output="obj/china-config/$suite/$arch"
mkdir -p "$output"
apkg build --path ../anduinos-apt-config-for-china --distro anduinos \
    --suite "$suite" --arch "$arch" --output "$PWD/$output/build"
packages=("$output"/build/*.deb)
[[ ${#packages[@]} == 1 && -f "${packages[0]}" ]]
cp "${packages[0]}" "$output/china-apt-config.deb"
