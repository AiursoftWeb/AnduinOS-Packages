#!/usr/bin/env bash
# Pre-build: downloads AppIndicator and KStatusNotifierItem Support extension.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"

UUID="appindicatorsupport@rgcjonas.gmail.com"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}appindicatorsupport@rgcjonas.gmail.com/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
