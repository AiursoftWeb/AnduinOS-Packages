#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FLUENT_ICON_COMMIT="8a99a6d"

rm -rf "$SCRIPT_DIR/deploy" /tmp/Fluent-icon-theme
git clone https://github.com/vinceliuice/Fluent-icon-theme.git /tmp/Fluent-icon-theme
git -C /tmp/Fluent-icon-theme checkout "$FLUENT_ICON_COMMIT"

echo "Copying source to deploy/src/..."
mkdir -p "$SCRIPT_DIR/deploy/src"
cp -r /tmp/Fluent-icon-theme/* "$SCRIPT_DIR/deploy/src/"

rm -rf /tmp/Fluent-icon-theme
echo "Done."
