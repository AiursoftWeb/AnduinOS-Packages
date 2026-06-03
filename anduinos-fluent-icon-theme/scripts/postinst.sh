#!/bin/sh
set -e

SRC="/usr/share/anduinos-fluent-icon-theme/src"

echo "Installing Fluent icon theme..."
cd "$SRC"
./install.sh --all -d /usr/share/icons

echo "Installing Fluent cursor theme..."
cd "$SRC/cursors"
./install.sh

# Rebuild icon caches — install.sh runs gtk-update-icon-cache
# internally but suppresses errors (> /dev/null 2>&1 || true).
# We rebuild explicitly for every icon theme with an index.theme.
echo "Rebuilding icon caches..."
for theme in /usr/share/icons/Fluent*; do
    if [ -f "$theme/index.theme" ]; then
        gtk-update-icon-cache -f "$theme"
    fi
done
echo "Fluent icon theme installed."
