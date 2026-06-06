#!/bin/sh
set -e
chmod 0755 /usr/share/gnome-shell/extensions/ding@rastersoft.com/app/ding.js
glib-compile-schemas /usr/share/gnome-shell/extensions/ding@rastersoft.com/schemas/
