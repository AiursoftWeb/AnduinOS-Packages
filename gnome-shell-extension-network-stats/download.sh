#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="network-stats@gnome.noroadsleft.xyz"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    if [[ "$TARGET" -ge 51 ]]; then
        # GNOME 51 removed St.BoxLayout.vertical; preserve older suite payloads.
        python3 - "$DEPLOY_DIR/lib/ui" <<'PYTHON'
from pathlib import Path
import sys

for name in ("PopupView.js", "ExpandableDeviceMenuItem.js"):
    path = Path(sys.argv[1]) / name
    source = path.read_text()
    old = "vertical: false"
    if source.count(old) != 1:
        raise SystemExit(f"Network Stats orientation patch no longer matches {name}")
    path.write_text(source.replace(old, "orientation: Clutter.Orientation.HORIZONTAL"))

# St.Widget no longer supplies the parent event handler. Unhandled events
# should keep propagating; the extension's right-click handling stays intact.
path = Path(sys.argv[1]) / "PopupView.js"
source = path.read_text()
old = "return super.vfunc_event(event);"
if source.count(old) != 1:
    raise SystemExit("Network Stats event patch no longer matches upstream")
path.write_text(source.replace(old, "return Clutter.EVENT_PROPAGATE;"))
PYTHON
    fi
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}network-stats@gnome.noroadsleft.xyz/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
