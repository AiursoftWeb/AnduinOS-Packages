#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FLUENT_GTK_COMMIT="9fc5291"   # pinned for supply-chain safety

rm -rf "$SCRIPT_DIR/deploy" /tmp/Fluent-gtk-theme
git clone https://github.com/vinceliuice/Fluent-gtk-theme.git /tmp/Fluent-gtk-theme
git -C /tmp/Fluent-gtk-theme checkout "$FLUENT_GTK_COMMIT"

echo "Installing sassc..."
sudo apt-get update -qq && sudo apt-get install -y -qq sassc 2>/dev/null || true

echo "Building theme to staging..."
cd /tmp/Fluent-gtk-theme
./install.sh --tweaks noborder round --theme all -d "$SCRIPT_DIR/deploy/themes"

rm -rf /tmp/Fluent-gtk-theme
echo "Done."
