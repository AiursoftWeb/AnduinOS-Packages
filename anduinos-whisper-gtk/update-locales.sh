#!/usr/bin/env bash
# Extract GTK and Shell-extension messages and merge them into catalogs.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pot="$root/po/anduinos-whisper-gtk.pot"

cd "$root"
xgettext --language=Python --keyword=_ --from-code=UTF-8 \
    --no-wrap \
    --package-name=anduinos-whisper-gtk --output="$pot" \
    src/anduinos_whisper_gtk/*.py
xgettext --join-existing --language=JavaScript --keyword=_ --keyword=N_ \
    --from-code=UTF-8 --no-wrap --package-name=anduinos-whisper-gtk \
    --output="$pot" \
    data/voice-typing@anduinos.com/extension.js

shopt -s nullglob
for catalog in po/*.po; do
    msgmerge --quiet --update --backup=none "$catalog" "$pot"
done
