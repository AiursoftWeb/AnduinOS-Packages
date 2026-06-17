#!/bin/sh
set -e

ARCHIVE="/usr/share/anduinos-fluent-icon-theme/fluent-icon-theme.tar.gz"
WORKDIR="$(mktemp -d /tmp/anduinos-fluent-icon-theme.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Extracting Fluent icon theme sources..."
tar -xzf "$ARCHIVE" -C "$WORKDIR"
rm -f "$ARCHIVE"

SRC="$WORKDIR/Fluent-icon-theme"

echo "Installing Fluent icon theme..."
(
    cd "$SRC" && \
    ./install.sh --all -d /usr/share/icons
)

echo "Installing Fluent cursor theme..."
(
    cd "$SRC/cursors" && \
    ./install.sh
)

echo "Rebuilding icon caches..."
for theme in /usr/share/icons/*; do
    if [ -d "$theme" ]; then
        gtk-update-icon-cache -f -t "$theme" || true
    fi
done
echo "Fluent icon theme installed."
