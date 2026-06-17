#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/ding@rastersoft.com/schemas/
dconf update
