#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/obj/locale"
DOMAIN="anduinos-btrfs-snapshots-manager"

mkdir -p "$OUT_DIR"
# The package includes this whole directory, so a previous product-domain MO
# must not survive an incremental build and leak into the renamed package.
find "$OUT_DIR" -type f -name '*.mo' ! -name "$DOMAIN.mo" -delete
for po_file in "$PO_DIR"/*.po; do
    locale_name="$(basename "$po_file" .po)"
    target="$OUT_DIR/$locale_name/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$po_file" -o "$target/$DOMAIN.mo"
    chmod 0644 "$target/$DOMAIN.mo"
done
