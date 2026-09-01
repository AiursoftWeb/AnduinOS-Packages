#!/usr/bin/env bash
# Compile the Control Panel's maintained gettext catalogs for packaging.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
domain="anduinos-control-panel"
po_dir="$root/po"
locale_dir="$root/locale"

rm -rf "$locale_dir"
shopt -s nullglob
catalogs=("$po_dir"/*.po)
if (( ${#catalogs[@]} == 0 )); then
    echo "No Control Panel translation catalogs found." >&2
    exit 1
fi
for catalog in "${catalogs[@]}"; do
    locale="$(basename "$catalog" .po)"
    target="$locale_dir/$locale/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$catalog" -o "$target/$domain.mo"
done
