#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/gnome-versions.sh"
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd patch patch

UUID="customize-ibus@hollowman.ml"

for SUITE in "${!GNOME_TARGETS[@]}"; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$SCRIPT_DIR/../lib/resolve-gnome-ext.py" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"

    # GNOME 46 uses the upstream v90 source layout; newer releases use v93+.
    PATCH="restart-requests.patch"
    if [ "$TARGET" -eq 46 ]; then
        PATCH="restart-requests-gnome46.patch"
    fi
    echo "[$SUITE] Preventing IBus restart requests from replaying on startup..."
    patch --batch --forward --fuzz=0 --directory="$DEPLOY_DIR" --strip=1 \
        < "$SCRIPT_DIR/patches/$PATCH"
done

echo "Done."

# Pre-compile GSettings schemas at build time so postinst is unnecessary
for suite_dir in deploy/*/; do
    schema_dir="${suite_dir}customize-ibus@hollowman.ml/schemas"
    [ -d "$schema_dir" ] && glib-compile-schemas "$schema_dir" || true
done
