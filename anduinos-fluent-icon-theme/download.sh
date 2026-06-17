#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FLUENT_ICON_COMMIT="8a99a6d"

rm -rf "$SCRIPT_DIR/deploy" /tmp/Fluent-icon-theme
git clone https://gitlab.aiursoft.com/mirror/fluent-icon-theme/ /tmp/Fluent-icon-theme
git -C /tmp/Fluent-icon-theme checkout "$FLUENT_ICON_COMMIT"

echo "Packing full upstream repo to deploy/fluent-icon-theme.tar.gz..."
mkdir -p "$SCRIPT_DIR/deploy"
tar -czf "$SCRIPT_DIR/deploy/fluent-icon-theme.tar.gz" --exclude='.git' -C /tmp Fluent-icon-theme

rm -rf /tmp/Fluent-icon-theme
echo "Done."
