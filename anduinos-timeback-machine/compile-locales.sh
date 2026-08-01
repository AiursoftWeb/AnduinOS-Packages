#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
po_dir="$project_dir/po"
locale_dir="$project_dir/locale"
domain="anduinos-timeback-machine"

expected_languages=(
    ar da de el en_GB es fi fr hi id it ja ko nl pl pt pt_BR ro ru sv
    th tr uk vi zh_CN zh_HK zh_TW
)
actual_languages=()
for po_file in "$po_dir"/*.po; do
    actual_languages+=("$(basename "$po_file" .po)")
done
if [[ "${actual_languages[*]}" != "${expected_languages[*]}" ]]; then
    echo "Timeback PO language matrix does not match the supported list." >&2
    echo "Expected: ${expected_languages[*]}" >&2
    echo "Actual:   ${actual_languages[*]}" >&2
    exit 1
fi

rm -rf "$locale_dir"

compiled=0
for po_file in "$po_dir"/*.po; do
    untranslated="$(
        msgattrib --untranslated --no-obsolete --no-wrap "$po_file"
    )"
    if grep -q '^msgid "[^"]' <<<"$untranslated"; then
        echo "Untranslated Timeback messages remain in $po_file." >&2
        exit 1
    fi
    language="$(basename "$po_file" .po)"
    target="$locale_dir/$language/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$po_file" \
        --output-file="$target/$domain.mo"
    compiled=$((compiled + 1))
done

echo "Compiled $compiled Timeback locale catalog(s)."
