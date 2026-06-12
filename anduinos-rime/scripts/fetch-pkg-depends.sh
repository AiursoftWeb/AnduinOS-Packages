#!/bin/bash
set -euo pipefail

# Lock absolute project root before any cd
BASE_DIR=$(pwd)
DEST="$BASE_DIR/assets/pkg_depends"

MIRROR="http://archive.ubuntu.com/ubuntu"
RELEASE="noble"

echo "[anduinos-rime] Looking up language-selector-common in Ubuntu $RELEASE archive..."

# Parse the Packages index to find the file path — no apt or root needed
PKG_PATH=$(wget -q -O - "$MIRROR/dists/$RELEASE/main/binary-amd64/Packages.gz" \
    | gunzip \
    | awk '/^Package: language-selector-common$/ { found=1; next }
           found && /^Filename:/ { print $2; exit }')

if [ -z "$PKG_PATH" ]; then
    echo "[anduinos-rime] ERROR: Could not find language-selector-common in Ubuntu archive"
    exit 1
fi

echo "[anduinos-rime] Resolved to: $PKG_PATH"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "[anduinos-rime] Downloading..."
wget -q "$MIRROR/$PKG_PATH" -O "$TMPDIR/language-selector-common.deb"

echo "[anduinos-rime] Extracting pkg_depends..."
dpkg-deb -x "$TMPDIR/language-selector-common.deb" "$TMPDIR/extracted"

SRC="$TMPDIR/extracted/usr/share/language-selector/data/pkg_depends"
mkdir -p "$(dirname "$DEST")"

echo "[anduinos-rime] Patching pkg_depends: removing im:zh entries, adding anduinos-rime..."
sed '/^im:zh/d' "$SRC" > "$DEST"
cat >> "$DEST" << 'EOF'
im:zh-hans:ibus:anduinos-rime
im:zh-hant:ibus:anduinos-rime
EOF

echo "[anduinos-rime] Done — pkg_depends regenerated from latest upstream at $(date -u)"
