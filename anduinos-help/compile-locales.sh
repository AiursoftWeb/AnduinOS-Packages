#!/usr/bin/env bash
# Compile .po → .mo for all supported locales.
# Mirrors the layout used by anduinos-appearance and anduinos-driver-center.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/locale"

rm -rf "$OUT_DIR"

shopt -s nullglob
for po in "$PO_DIR"/*.po; do
    locale_name=$(basename "$po" .po)
    target="$OUT_DIR/$locale_name/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt "$po" -o "$target/anduinos-help.mo"
    echo "  $locale_name → $target/anduinos-help.mo"
done

echo "Compiled $(ls "$PO_DIR"/*.po 2>/dev/null | wc -l) locales."
