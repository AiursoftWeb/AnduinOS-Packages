#!/bin/sh
set -eu

for po_file in po/*.po; do
    locale_name="$(basename "$po_file" .po)"
    output_dir="locale/$locale_name/LC_MESSAGES"
    mkdir -p "$output_dir"
    msgfmt --check --output-file="$output_dir/anduinos-driver-center.mo" "$po_file"
done
