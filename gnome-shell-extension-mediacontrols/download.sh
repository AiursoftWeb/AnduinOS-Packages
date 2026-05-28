#!/usr/bin/env bash
# Pre-build: downloads extension for each supported suite/GNOME version.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOLVER="$SCRIPT_DIR/../lib/resolve-gnome-ext.py"

UUID="mediacontrols@cliffniff.github.com"

declare -A GNOME_TARGETS=(
    [noble]=46
    [questing]=49
    [resolute]=50
)

for SUITE in noble questing resolute; do
    TARGET=${GNOME_TARGETS[$SUITE]}
    DEPLOY_DIR="deploy/$SUITE/$UUID"

    rm -rf "$DEPLOY_DIR"
    mkdir -p "$DEPLOY_DIR"

    echo "[$SUITE] Resolving $UUID for GNOME $TARGET..."
    python3 "$RESOLVER" "$UUID" --target "$TARGET" --download --out "$DEPLOY_DIR"
done

echo "Done."
