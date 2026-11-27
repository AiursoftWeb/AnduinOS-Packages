#!/usr/bin/env bash
# Pre-build: downloads pinned Desktop Icons NG (DING) archives verified by
# SHA-256 (v85 GTK 3 for GNOME 46 on noble; v105 GTK 4 with Wayland/D-Bus
# root-cause patches for GNOME 50/51 on resolute and stonking).
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../lib/build-guards.sh"
need_cmd curl curl
need_cmd unzip unzip
need_cmd patch patch
need_cmd python3 python3
need_cmd glib-compile-schemas libglib2.0-bin

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
archive_v85="$(mktemp)"
archive_v105="$(mktemp)"
trap 'rm -f "$archive_v85" "$archive_v105"' EXIT

curl --fail --location --silent --show-error --retry 3 \
    'https://extensions.gnome.org/review/download/72024.shell-extension.zip' \
    --output "$archive_v85"
printf '%s  %s\n' \
    '429171dda75a6d449650679c77370fb7edd17b9b183d68e54ab6892ab478a3f3' \
    "$archive_v85" | sha256sum --check --status

curl --fail --location --silent --show-error --retry 3 \
    'https://extensions.gnome.org/review/download/78123.shell-extension.zip' \
    --output "$archive_v105"
printf '%s  %s\n' \
    '84d9bf55afc5d050bd1eb10fa19019b23625e69b86c347d6ec3355e1708d4896' \
    "$archive_v105" | sha256sum --check --status

for suite in noble resolute stonking; do
    destination="$script_dir/deploy/$suite/ding@rastersoft.com"
    rm -rf "$destination"
    mkdir -p "$destination"

    if [[ "$suite" == noble ]]; then
        archive="$archive_v85"
        expected_version=85
    else
        archive="$archive_v105"
        expected_version=105
    fi

    unzip -q "$archive" -d "$destination"
    # Normalize archive modes so system extension files are world-readable after
    # dpkg installs them as root.
    find "$destination" -type d -exec chmod 755 {} +
    find "$destination" -type f -exec chmod 644 {} +

    python3 - "$destination/metadata.json" "$suite" "$expected_version" <<'PYEOF'
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
shell = {"noble": "46", "resolute": "50", "stonking": "51"}[sys.argv[2]]
expected_version = int(sys.argv[3])
if (metadata.get("uuid"), metadata.get("version")) != ("ding@rastersoft.com", expected_version):
    raise SystemExit(f"Downloaded DING for {sys.argv[2]} is not version {expected_version}")
if shell not in metadata.get("shell-version", []):
    raise SystemExit(f"DING v{expected_version} does not declare GNOME Shell {shell} support")
PYEOF

    if [[ "$suite" == resolute || "$suite" == stonking ]]; then
        for patch_file in \
            "$script_dir/patches/01-avoid-startup-dbus-blocking-and-watchdog-livelock.patch" \
            "$script_dir/patches/02-stabilize-wayland-desktop-window-and-inhibit-suspend.patch" \
            "$script_dir/patches/03-reuse-grid-windows-and-fix-wayland-scaling.patch"; do
            patch --batch --forward --fuzz=0 --directory="$destination" --strip=1 < "$patch_file"
        done
    fi

    chmod 755 "$destination/app/ding.js"
    glib-compile-schemas "$destination/schemas"
    chmod 644 "$destination/schemas/gschemas.compiled"
done
