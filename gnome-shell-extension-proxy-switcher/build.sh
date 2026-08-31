#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSION="$ROOT/assets/proxy-switcher@anduinos.com"
SCHEMAS="$EXTENSION/schemas"
LOCALE="$EXTENSION/locale"
DOMAIN='proxy-switcher@anduinos.com'

bash "$ROOT/scripts/check.sh"
glib-compile-schemas --strict "$SCHEMAS"

while IFS= read -r -d '' po_file; do
    language="$(basename "$po_file" .po)"
    target="$LOCALE/$language/LC_MESSAGES/$DOMAIN.mo"
    mkdir -p "$(dirname "$target")"
    msgfmt --check --check-format "$po_file" --output-file="$target"
done < <(find "$ROOT/po" -maxdepth 1 -type f -name '*.po' -print0 | sort -z)

source_catalogs="$(find "$ROOT/po" -maxdepth 1 -type f -name '*.po' | wc -l)"
compiled_catalogs="$(find "$LOCALE" -type f -name '*.mo' | wc -l)"
test "$compiled_catalogs" -eq "$source_catalogs"
test -s "$SCHEMAS/gschemas.compiled"

echo "Built $compiled_catalogs translation catalogs and the extension schema"
