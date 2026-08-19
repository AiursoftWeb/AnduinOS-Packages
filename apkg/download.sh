#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd curl
need_cmd sha256sum coreutils
need_cmd unzip

VERSION="10.0.54"
PACKAGE="aiursoft.apkg.client.${VERSION}.nupkg"
PACKAGE_URL="https://api.nuget.org/v3-flatcontainer/aiursoft.apkg.client/${VERSION}/${PACKAGE}"
PACKAGE_SHA256="3ddd8b2b87e154f2c28ab6321ace7d5984cd11eb45e8a87447676025b0fa9b24"
SOURCE_COMMIT="ac82183a21e53d3cd910005606196c6c6a386f5a"
LICENSE_URL="https://gitlab.aiursoft.com/aiursoft/apkg/-/raw/${SOURCE_COMMIT}/LICENSE"
LICENSE_SHA256="bd4349a7d3733577855e0d61f7cb4bd1675beec1e379da490b3694957501fff2"
CACHE_DIR="$SCRIPT_DIR/deploy/cache"
TEMPORARY=""

cleanup() {
    if [[ -n "$TEMPORARY" && -d "$TEMPORARY" ]]; then
        rm -rf -- "$TEMPORARY"
    fi
}
trap cleanup EXIT

die() {
    printf 'download.sh: %s\n' "$1" >&2
    exit 1
}

verify() {
    local file="$1" expected="$2" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]] || die "SHA-256 mismatch for $file (expected $expected, got $actual)"
}

fetch() {
    local url="$1" output="$2" expected="$3"
    mkdir -p "$(dirname "$output")"
    if [[ -f "$output" ]]; then
        verify "$output" "$expected"
        printf 'Using verified cache: %s\n' "$output"
        return
    fi
    printf 'Downloading %s\n' "$url"
    curl --fail --location --retry 3 --output "$output.tmp" "$url"
    verify "$output.tmp" "$expected"
    mv "$output.tmp" "$output"
}

main() {
    local archive="$CACHE_DIR/$PACKAGE"
    local license="$CACHE_DIR/LICENSE-$SOURCE_COMMIT"
    fetch "$PACKAGE_URL" "$archive" "$PACKAGE_SHA256"
    fetch "$LICENSE_URL" "$license" "$LICENSE_SHA256"

    TEMPORARY="$(mktemp -d)"
    unzip -q "$archive" 'tools/net10.0/any/*' -d "$TEMPORARY"

    [[ -f "$TEMPORARY/tools/net10.0/any/apkg.dll" ]] || die "NuGet package does not contain apkg.dll"
    [[ -f "$TEMPORARY/tools/net10.0/any/apkg.deps.json" ]] || die "NuGet package does not contain apkg.deps.json"
    [[ -f "$TEMPORARY/tools/net10.0/any/apkg.runtimeconfig.json" ]] || die "NuGet package does not contain apkg.runtimeconfig.json"

    rm -rf -- "$SCRIPT_DIR/deploy/app"
    mkdir -p "$SCRIPT_DIR/deploy/app"
    cp -a "$TEMPORARY/tools/net10.0/any/." "$SCRIPT_DIR/deploy/app/"
    rm -rf -- "$SCRIPT_DIR/deploy/app/runtimes" "$SCRIPT_DIR/deploy/app/DotnetToolSettings.xml"
    find "$SCRIPT_DIR/deploy/app" -type f -name '*.pdb' -delete
    install -m 0644 "$license" "$SCRIPT_DIR/deploy/LICENSE"
}

main "$@"
