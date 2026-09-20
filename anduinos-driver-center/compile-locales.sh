#!/bin/sh
set -eu

for po_file in po/*.po; do
    locale_name="$(basename "$po_file" .po)"
    if [ "$locale_name" != "en_US" ]; then
        msgcmp --use-untranslated --no-fuzzy-matching \
            "$po_file" po/anduinos-driver-center.pot
        for selector in --untranslated --only-fuzzy; do
            if msgattrib "$selector" --no-obsolete "$po_file" \
                | awk '/^msgid / { count += 1 } END { exit count > 1 ? 0 : 1 }'; then
                echo "$selector messages remain in $po_file." >&2
                exit 1
            fi
        done
    fi
    output_dir="locale/$locale_name/LC_MESSAGES"
    mkdir -p "$output_dir"
    msgfmt --check --output-file="$output_dir/anduinos-driver-center.mo" "$po_file"
done
