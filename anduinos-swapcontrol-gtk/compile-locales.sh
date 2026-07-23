#!/usr/bin/env bash
# Compile .po → .mo for all supported locales
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/locale"

rm -rf "$OUT_DIR"

for po in "$PO_DIR"/*.po; do
    locale_name=$(basename "$po" .po)
    target="$OUT_DIR/$locale_name/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt "$po" -o "$target/swapcontrol-gtk.mo"
    echo "  $locale_name → $target/swapcontrol-gtk.mo"
done

echo "Compiled $(ls "$PO_DIR"/*.po | wc -l) locales."
