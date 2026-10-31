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
    if [[ "$locale" != "en_US" ]]; then
        msgcmp --use-untranslated --no-fuzzy-matching \
            "$catalog" "$po_dir/$domain.pot"
        if msgattrib --untranslated --no-obsolete "$catalog" \
            | awk '/^msgid / { count += 1 } END { exit count > 1 ? 0 : 1 }'; then
            echo "Untranslated Control Panel messages remain in $catalog." >&2
            exit 1
        fi
        if msgattrib --only-fuzzy --no-obsolete "$catalog" \
            | awk '/^msgid / { count += 1 } END { exit count > 1 ? 0 : 1 }'; then
            echo "Fuzzy Control Panel messages remain in $catalog." >&2
            exit 1
        fi
    fi
    target="$locale_dir/$locale/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$catalog" -o "$target/$domain.mo"
done
