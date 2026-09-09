#!/usr/bin/env bash
# Extract every marked user-facing Python string and merge it into catalogs.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pot="$root/po/anduinos-control-panel.pot"

cd "$root"
xgettext --language=Python --keyword=_ --keyword=N_ --from-code=UTF-8 \
    --no-wrap \
    --package-name=anduinos-control-panel --output="$pot" \
    src/anduinos_control_panel/*.py
xgettext --join-existing --language=Desktop --from-code=UTF-8 --no-wrap \
    --package-name=anduinos-control-panel --output="$pot" \
    data/com.anduinos.ControlPanel.desktop

shopt -s nullglob
for catalog in po/*.po; do
    msgmerge --quiet --update --backup=none "$catalog" "$pot"
done
