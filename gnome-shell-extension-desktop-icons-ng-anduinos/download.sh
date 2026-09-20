#!/usr/bin/env bash
# Pre-build: downloads Desktop Icons NG (DING) for each supported suite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="ding@rastersoft.com"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    # ── DING v84 spawns ding.js as a child process, needs +x ──────────
    chmod +x "$DEPLOY_DIR/app/ding.js"
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}ding@rastersoft.com/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
