#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/lockkeys@vaina.lt/schemas/
dconf update
