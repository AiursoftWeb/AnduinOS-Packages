#!/usr/bin/env bash
# Pin the last GTK 3 DING release advertised for GNOME Shell 50.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
archive="$(mktemp)"
trap 'rm -f "$archive"' EXIT

curl --fail --location --silent --show-error --retry 3 \
    'https://extensions.gnome.org/review/download/72024.shell-extension.zip' \
    --output "$archive"
printf '%s  %s\n' \
    '429171dda75a6d449650679c77370fb7edd17b9b183d68e54ab6892ab478a3f3' \
    "$archive" | sha256sum --check --status

for suite in noble resolute; do
    destination="$script_dir/deploy/$suite/ding@rastersoft.com"
    rm -rf "$destination"
    mkdir -p "$destination"
    unzip -q "$archive" -d "$destination"
    # The upstream ZIP marks metadata.json private; system extensions must be
    # readable by every desktop user after dpkg installs them as root.
    find "$destination" -type d -exec chmod 755 {} +
    find "$destination" -type f -exec chmod 644 {} +
    python3 - "$destination/metadata.json" "$suite" <<'PY'
import json
import sys

from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
shell = {"noble": "46", "resolute": "50"}[sys.argv[2]]
if (metadata.get("uuid"), metadata.get("version")) != ("ding@rastersoft.com", 85):
    raise SystemExit("Downloaded DING is not version 85")
if shell not in metadata.get("shell-version", []):
    raise SystemExit(f"DING v85 does not declare GNOME Shell {shell} support")
PY
    chmod +x "$destination/app/ding.js"
    glib-compile-schemas "$destination/schemas"
done
