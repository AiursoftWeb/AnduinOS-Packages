#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/locale"
DOMAIN="anduinos-yubikey-manager"

mkdir -p "$OUT_DIR"
for po in "$PO_DIR"/*.po; do
    locale_name="$(basename "$po" .po)"
    target="$OUT_DIR/$locale_name/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check "$po" -o "$target/$DOMAIN.mo"
    echo "  $locale_name → $target/$DOMAIN.mo"
done
