#!/usr/bin/env bash
set -euo pipefail
umask 022
cd "$(dirname "$0")"
suite="$1"
case "$suite" in
    noble-addon|resolute-addon|stonking-addon) ;;
    *) echo 'Unsupported OOBE target' >&2; exit 1 ;;
esac
for package in anduinos-apt-config anduinos-apt-config-for-china; do
    for arch in amd64 arm64; do
        output="obj/source-config/$suite/$package/$arch"
        mkdir -p "$output"
        apkg build --path "../$package" --distro anduinos \
            --suite "$suite" --arch "$arch" --output "$PWD/$output"
        files=("$output"/*.deb)
        [[ ${#files[@]} == 1 && -f "${files[0]}" ]]
        mkdir -p "obj/source-payload/$suite/$package"
        install -m 644 "${files[0]}" "obj/source-payload/$suite/$package/$arch.deb"
    done
done
