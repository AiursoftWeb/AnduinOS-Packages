#!/bin/bash
set -euo pipefail

echo "[anduinos-rime] Updating apt lists..."
apt-get update -qq 2>/dev/null || echo "[anduinos-rime] (non-root, assuming apt lists are current)"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "[anduinos-rime] Downloading language-selector-common..."
cd "$TMPDIR"
apt download language-selector-common

echo "[anduinos-rime] Extracting pkg_depends..."
dpkg-deb -x language-selector-common_*.deb extracted

SRC="extracted/usr/share/language-selector/data/pkg_depends"
DEST="$OLDPWD/assets/pkg_depends"
mkdir -p "$(dirname "$DEST")"

echo "[anduinos-rime] Patching pkg_depends: removing im:zh entries, adding anduinos-rime..."
sed '/^im:zh/d' "$SRC" > "$DEST"
cat >> "$DEST" << 'EOF'
im:zh-hans:ibus:anduinos-rime
im:zh-hant:ibus:anduinos-rime
EOF

echo "[anduinos-rime] Done — pkg_depends regenerated from latest upstream at $(date -u)"
