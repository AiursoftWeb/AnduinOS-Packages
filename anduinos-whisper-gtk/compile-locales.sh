#!/usr/bin/env bash
# Compile Voice Typing catalogs for both GTK and GNOME Shell consumers.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
domain="anduinos-whisper-gtk"
po_dir="$root/po"
locale_dir="$root/locale"

rm -rf "$locale_dir"
shopt -s nullglob
catalogs=("$po_dir"/*.po)
if (( ${#catalogs[@]} == 0 )); then
    echo "No Voice Typing translation catalogs found." >&2
    exit 1
fi
for catalog in "${catalogs[@]}"; do
    locale="$(basename "$catalog" .po)"
    if [[ "$locale" != "en_US" ]]; then
        msgcmp --use-untranslated --no-fuzzy-matching \
            "$catalog" "$po_dir/$domain.pot"
        for selector in --untranslated --only-fuzzy; do
            if msgattrib "$selector" --no-obsolete "$catalog" \
                | awk '/^msgid / { count += 1 } END { exit count > 1 ? 0 : 1 }'; then
                echo "$selector messages remain in $catalog." >&2
                exit 1
            fi
        done
    fi
    target="$locale_dir/$locale/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$catalog" -o "$target/$domain.mo"
done
