#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FLUENT_GTK_COMMIT="9fc5291"

rm -rf "$SCRIPT_DIR/deploy" /tmp/Fluent-gtk-theme
git clone https://gitlab.aiursoft.com/mirror/fluent-gtk-theme/ /tmp/Fluent-gtk-theme
git -C /tmp/Fluent-gtk-theme checkout "$FLUENT_GTK_COMMIT"

echo "Copying source to deploy/src/..."
rm -rf /tmp/Fluent-gtk-theme/links
mkdir -p "$SCRIPT_DIR/deploy/src"
cp -r /tmp/Fluent-gtk-theme/* "$SCRIPT_DIR/deploy/src/"

rm -rf /tmp/Fluent-gtk-theme
echo "Done."
