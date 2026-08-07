#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
FONT_SOURCE="/usr/share/fonts/opentype/unifont/unifont.otf"
FONT_OUTPUT="$SCRIPT_DIR/assets/anduinos-unicode-28.pf2"
EXPECTED_UNIFONT_VERSION="1:16.0.04-1build1"
EXPECTED_GRUB_VERSION="2.14-2ubuntu2.1"
EXPECTED_SOURCE_SHA256="0e3981ab552231b5a2a870f2b61741903a4bf25c23ef5aeb05fdced1b3c7af4d"
EXPECTED_SHA256="112ceb12fb241561cb7e710b324536e9f6cb2d86d02683cf0b23bc11de9acea4"

check_package_version() {
    local package="$1"
    local expected="$2"
    local actual

    actual="$(dpkg-query -W -f='${Version}' "$package" 2>/dev/null || true)"
    if [ "$actual" != "$expected" ]; then
        echo "Expected $package $expected, found ${actual:-not installed}." >&2
        exit 1
    fi
}

check_package_version fonts-unifont "$EXPECTED_UNIFONT_VERSION"
check_package_version grub2-common "$EXPECTED_GRUB_VERSION"

if [ ! -f "$FONT_SOURCE" ]; then
    echo "GNU Unifont source not found: $FONT_SOURCE" >&2
    exit 1
fi

mkdir -p "$(dirname "$FONT_OUTPUT")"
source_sha256="$(sha256sum "$FONT_SOURCE" | cut -d ' ' -f 1)"
if [ "$source_sha256" != "$EXPECTED_SOURCE_SHA256" ]; then
    echo "GNU Unifont source checksum mismatch: $source_sha256" >&2
    exit 1
fi

temporary_output="$(mktemp "$SCRIPT_DIR/assets/.anduinos-unicode-28.XXXXXX.pf2")"
cleanup() {
    rm -f "$temporary_output"
}
trap cleanup EXIT

grub-mkfont \
    --size=28 \
    --output="$temporary_output" \
    "$FONT_SOURCE"

actual_sha256="$(sha256sum "$temporary_output" | cut -d ' ' -f 1)"
if [ "$actual_sha256" != "$EXPECTED_SHA256" ]; then
    echo "Generated font checksum mismatch: $actual_sha256" >&2
    exit 1
fi

chmod 0644 "$temporary_output"
mv "$temporary_output" "$FONT_OUTPUT"
trap - EXIT

echo "Generated $FONT_OUTPUT"
echo "SHA256: $actual_sha256"
