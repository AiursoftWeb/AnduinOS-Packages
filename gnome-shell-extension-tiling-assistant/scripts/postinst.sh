#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/tiling-assistant@leleat-on-github/schemas/
dconf update
