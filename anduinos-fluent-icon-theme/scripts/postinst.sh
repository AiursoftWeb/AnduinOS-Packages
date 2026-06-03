#!/bin/sh
set -e

ARCHIVE="/usr/share/anduinos-fluent-icon-theme/fluent-icon-theme.tar.gz"
WORKDIR="$(mktemp -d /tmp/anduinos-fluent-icon-theme.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Extracting Fluent icon theme sources..."
tar -xzf "$ARCHIVE" -C "$WORKDIR"

SRC="$WORKDIR/Fluent-icon-theme"

echo "Installing Fluent icon theme..."
cd "$SRC"
./install.sh -d /usr/share/icons

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
