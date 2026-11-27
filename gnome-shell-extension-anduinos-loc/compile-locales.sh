#!/usr/bin/env bash
# Compile .po -> .mo for all supported locales
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PO_DIR="$SCRIPT_DIR/po"
OUT_DIR="$SCRIPT_DIR/assets/loc@anduinos.com/locale"
DOMAIN="loc@anduinos.com"

rm -rf "$OUT_DIR"

while IFS= read -r -d '' po_file; do
    locale_name="$(basename "$po_file" .po)"
    target="$OUT_DIR/$locale_name/LC_MESSAGES/$DOMAIN.mo"
    mkdir -p "$(dirname "$target")"
    msgfmt --check --check-format "$po_file" --output-file="$target"
done < <(find "$PO_DIR" -maxdepth 1 -type f -name '*.po' -print0 | sort -z)

source_catalogs="$(find "$PO_DIR" -maxdepth 1 -type f -name '*.po' | wc -l)"
compiled_catalogs="$(find "$OUT_DIR" -type f -name '*.mo' | wc -l)"
test "$compiled_catalogs" -eq "$source_catalogs"

echo "Compiled $compiled_catalogs translation catalogs for $DOMAIN."
