#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
po_dir="$project_dir/po"
locale_dir="$project_dir/locale"
domain="anduinos-installer-beta"

mapfile -t expected_languages < <(
    python3 - "$project_dir/data/languages.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    policy = json.load(stream)
default = policy["default_language"]
for code in sorted(
    language["code"]
    for language in policy["languages"]
    if language["code"] != default
):
    print(code)
PY
)
actual_languages=()
for po_file in "$po_dir"/*.po; do
    actual_languages+=("$(basename "$po_file" .po)")
done
if [[ "${actual_languages[*]}" != "${expected_languages[*]}" ]]; then
    echo "Installer PO language matrix does not match the supported list." >&2
    echo "Expected: ${expected_languages[*]}" >&2
    echo "Actual:   ${actual_languages[*]}" >&2
    exit 1
fi

rm -rf "$locale_dir"

compiled=0
for po_file in "$po_dir"/*.po; do
    language="$(basename "$po_file" .po)"
    # An untranslated multi-line entry starts with `msgid ""`, just like the
    # catalog header. Count entries instead of grepping only single-line IDs.
    # The filtered catalog always contains one header; a second msgid means at
    # least one real untranslated message remains.
    if msgattrib --untranslated --no-obsolete --no-wrap "$po_file" \
        | awk '/^msgid / { count += 1 } END { exit count > 1 ? 0 : 1 }'; then
        echo "Untranslated installer messages remain in $po_file." >&2
        exit 1
    fi
    target="$locale_dir/$language/LC_MESSAGES"
    mkdir -p "$target"
    msgfmt --check --check-format "$po_file" \
        --output-file="$target/$domain.mo"
    compiled=$((compiled + 1))
done

echo "Compiled $compiled installer locale catalog(s)."
