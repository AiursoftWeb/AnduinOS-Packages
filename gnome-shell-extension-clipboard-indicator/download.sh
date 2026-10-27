#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="clipboard-indicator@tudmotu.com"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    if [[ "$TARGET" -ge 51 ]]; then
        # GNOME 51 removed the backend getter and St widget vertical property.
        # Keep older suites intact, and fail explicitly if upstream changes.
        python3 - "$DEPLOY_DIR" <<'PYTHON'
from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "keyboard.js"
source = path.read_text()
old = "Clutter.get_default_backend()"
new = "global.stage.context.get_backend()"
if source.count(old) != 1:
    raise SystemExit("Clipboard backend patch no longer matches upstream")
path.write_text(source.replace(old, new))
for name, horizontal, vertical in (("extension.js", 0, 2), ("confirmDialog.js", 1, 1)):
    path = root / name
    source = path.read_text()
    if source.count("vertical: false") != horizontal or source.count("vertical: true") != vertical:
        raise SystemExit(f"Clipboard orientation patch no longer matches {name}")
    source = source.replace("vertical: false", "orientation: Clutter.Orientation.HORIZONTAL")
    source = source.replace("vertical: true", "orientation: Clutter.Orientation.VERTICAL")
    path.write_text(source)
PYTHON
    fi
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}clipboard-indicator@tudmotu.com/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
