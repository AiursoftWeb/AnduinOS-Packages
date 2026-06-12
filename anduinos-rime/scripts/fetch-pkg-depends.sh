#!/bin/bash
set -euo pipefail

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

apt-get update -qq 2>/dev/null || true
cd "$TMPDIR"
apt download language-selector-common
dpkg-deb -x language-selector-common_*.deb extracted

SRC="extracted/usr/share/language-selector/data/pkg_depends"
DEST="$OLDPWD/assets/pkg_depends"
mkdir -p "$(dirname "$DEST")"

sed '/^im:zh/d' "$SRC" > "$DEST"
cat >> "$DEST" << 'EOF'
im:zh-hans:ibus:anduinos-rime
im:zh-hant:ibus:anduinos-rime
EOF

echo "[anduinos-rime] Regenerated pkg_depends from latest upstream"
