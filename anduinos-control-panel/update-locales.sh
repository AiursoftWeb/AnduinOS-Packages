#!/usr/bin/env bash
# Extract every marked user-facing Python string and merge it into catalogs.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pot="$root/po/anduinos-control-panel.pot"

xgettext --language=Python --keyword=_ --from-code=UTF-8 \
    --package-name=anduinos-control-panel --output="$pot" \
    "$root"/src/anduinos_control_panel/*.py

shopt -s nullglob
for catalog in "$root"/po/*.po; do
    msgmerge --quiet --update --backup=none "$catalog" "$pot"
done
