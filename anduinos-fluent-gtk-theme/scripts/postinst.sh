#!/bin/sh
set -e

SRC="/usr/share/anduinos-fluent-gtk-theme/src"

echo "Installing Fluent GTK theme..."
cd "$SRC"
./install.sh --tweaks noborder round --theme all -d /usr/share/themes

echo "Fluent GTK theme installed."
