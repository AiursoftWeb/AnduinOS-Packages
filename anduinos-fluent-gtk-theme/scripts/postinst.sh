#!/bin/sh
set -e

SRC="/usr/share/anduinos-fluent-gtk-theme/src"

echo "Installing sassc if needed..."
apt-get update -qq && apt-get install -y -qq sassc 2>/dev/null || true

echo "Installing Fluent GTK theme..."
cd "$SRC"
./install.sh --tweaks noborder round --theme all -d /usr/share/themes

echo "Fluent GTK theme installed."
