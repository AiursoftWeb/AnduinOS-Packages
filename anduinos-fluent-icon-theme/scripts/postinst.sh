#!/bin/sh
set -e

SRC="/usr/share/anduinos-fluent-icon-theme/src"

echo "Installing Fluent icon theme..."
cd "$SRC"
./install.sh --all -d /usr/share/icons

echo "Installing Fluent cursor theme..."
cd "$SRC/cursors"
./install.sh

echo "Updating icon caches..."
for theme in /usr/share/icons/Fluent*; do
    [ -d "$theme" ] && gtk-update-icon-cache -f -t "$theme" 2>/dev/null || true
done

echo "Fluent icon theme installed."
