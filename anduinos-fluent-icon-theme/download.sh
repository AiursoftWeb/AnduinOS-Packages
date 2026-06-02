#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FLUENT_ICON_COMMIT="8a99a6d"   # pinned for supply-chain safety

rm -rf "$SCRIPT_DIR/deploy" /tmp/Fluent-icon-theme
git clone https://github.com/vinceliuice/Fluent-icon-theme.git /tmp/Fluent-icon-theme
git -C /tmp/Fluent-icon-theme checkout "$FLUENT_ICON_COMMIT"

STAGING="$SCRIPT_DIR/deploy/icons"

echo "Installing icon theme to staging..."
cd /tmp/Fluent-icon-theme
./install.sh --all -d "$STAGING"

echo "Installing cursor theme..."
# cursor install.sh doesn't support -d; copy manually
cd /tmp/Fluent-icon-theme/cursors
cp -r dist "$STAGING/Fluent-cursors"
cp -r dist-dark "$STAGING/Fluent-dark-cursors"

# Convert any absolute symlinks to relative (or remove broken ones)
echo "Fixing symlinks..."
find "$STAGING" -type l -exec test ! -e {} \; -delete 2>/dev/null || true

rm -rf /tmp/Fluent-icon-theme
echo "Done."
