#!/usr/bin/env bash
# Compile .po -> .mo for all supported locales
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/build-guards.sh
source "$SCRIPT_DIR/../lib/build-guards.sh"

need_cmd msgfmt gettext

PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/locale"

rm -rf "$OUT_DIR"

if [ ! -d "$PO_DIR" ] || [ -z "$(ls -A "$PO_DIR"/*.po 2>/dev/null)" ]; then
    echo "No .po files found, skipping locale compilation."
    mkdir -p "$OUT_DIR"
    exit 0
fi

count=0
for po in "$PO_DIR"/*.po; do
    locale_name="$(basename "$po" .po)"
    target="$OUT_DIR/$locale_name/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$po" -o "$target/anduinos-copy-file-path.mo"
    count=$((count + 1))
done

echo "Compiled $count locales."
