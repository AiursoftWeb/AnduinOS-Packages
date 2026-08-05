#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/obj/locale"
DOMAIN="anduinos-waypoint-gtk"

mkdir -p "$OUT_DIR"
for po_file in "$PO_DIR"/*.po; do
    locale_name="$(basename "$po_file" .po)"
    target="$OUT_DIR/$locale_name/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$po_file" -o "$target/$DOMAIN.mo"
    chmod 0644 "$target/$DOMAIN.mo"
done
