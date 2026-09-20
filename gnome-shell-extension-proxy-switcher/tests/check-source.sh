#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTENSION="$ROOT/assets/proxy-switcher@anduinos.com"
SCHEMAS="$EXTENSION/schemas"

gjs -m "$ROOT/tests/proxy-state.test.js"
glib-compile-schemas --strict --dry-run "$SCHEMAS"

jq empty "$EXTENSION/metadata.json"

while IFS= read -r -d '' po_file; do
    msgfmt --check --check-format "$po_file" --output-file=/dev/null
done < <(find "$ROOT/po" -maxdepth 1 -type f -name '*.po' -print0 | sort -z)

echo 'Proxy source tests, schema, metadata syntax, and translation checks passed'
