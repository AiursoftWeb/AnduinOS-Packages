#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd curl
need_cmd sha256sum coreutils
need_cmd tar

CARAPACE_VERSION="1.7.3"
CARAPACE_BASE_URL="https://github.com/carapace-sh/carapace-bin/releases/download/v${CARAPACE_VERSION}"

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

prepare_carapace() {
    local arch="$1" archive sha256 cache temporary
    case "$arch" in
        amd64)
            sha256="35ab52bfe7bdd8296d90c3687660bde80497599badde840ab615d2f421f5f053"
            ;;
        arm64)
            sha256="b2456cb09d77004db87de2567d6d7588a61ceb4724522c463e2b1c1f87b4d4b9"
            ;;
        *) die "unsupported architecture: $arch" ;;
    esac
    archive="carapace-bin_${CARAPACE_VERSION}_linux_${arch}.tar.gz"
    cache="$SCRIPT_DIR/deploy/cache/$archive"
    fetch "$CARAPACE_BASE_URL/$archive" "$cache" "$sha256"
    temporary="$(mktemp -d)"
    tar -xzf "$cache" -C "$temporary"
    [[ -x "$temporary/carapace" ]] || die "Carapace archive has no executable carapace binary"
    [[ -f "$temporary/LICENSE" ]] || die "Carapace archive does not contain LICENSE"
    mkdir -p "$SCRIPT_DIR/deploy/$arch"
    install -m 0755 "$temporary/carapace" "$SCRIPT_DIR/deploy/$arch/carapace"
    install -m 0644 "$temporary/LICENSE" "$SCRIPT_DIR/deploy/$arch/CARAPACE-LICENSE"
    rm -rf -- "$temporary"
}

main() {
    [[ $# -eq 1 ]] || die "usage: $0 <amd64|arm64>"
    case $1 in
        amd64|arm64) ;;
        *) die "unsupported architecture: $1" ;;
    esac
    prepare_carapace "$1"
}

main "$@"
