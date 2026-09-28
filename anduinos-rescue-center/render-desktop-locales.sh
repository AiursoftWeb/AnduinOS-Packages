#!/bin/sh
set -eu

cd "$(dirname "$0")"
temporary="$(mktemp -d)"
trap 'rm -r "$temporary"' EXIT HUP INT TERM

for desktop in data/com.anduinos.RescueCenter.desktop data/anduinos-rescue-center-live-shortcut.desktop; do
    base="$(basename "$desktop")"
    sed '/^\(Name\|Comment\|Keywords\|Summary\|Description\)\[[^]]*\]=/d' \
        "$desktop" > "$temporary/$base"
    for po_file in po/*.po; do
        locale_name="$(sed -n 's/^"Language: \(.*\)\\n"$/\1/p' "$po_file" | head -n 1)"
        [ "$locale_name" = en_US ] && continue
        [ -n "$locale_name" ] || { echo "Missing Language header in $po_file" >&2; exit 1; }
        msgfmt --desktop --locale="$locale_name" \
            -kSummary -kDescription \
            --template="$temporary/$base" \
            --output-file="$temporary/next.desktop" "$po_file"
        mv "$temporary/next.desktop" "$temporary/$base"
    done
    cp "$temporary/$base" "$desktop"
done
