#!/usr/bin/env bash
# Compile .po → .mo for all supported locales
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/locale"

rm -rf "$OUT_DIR"

if [ ! -d "$PO_DIR" ] || [ -z "$(ls -A "$PO_DIR"/*.po 2>/dev/null)" ]; then
    echo "No .po files found, skipping locale compilation."
    mkdir -p "$OUT_DIR"
    exit 0
fi

for po in "$PO_DIR"/*.po; do
    locale_name=$(basename "$po" .po)
    target="$OUT_DIR/$locale_name/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt "$po" -o "$target/anduinos-exe-runner.mo"
    echo "  $locale_name → $target/anduinos-exe-runner.mo"
done

echo "Compiled $(ls "$PO_DIR"/*.po 2>/dev/null | wc -l) locales."
