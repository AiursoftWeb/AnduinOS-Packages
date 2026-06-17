#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/customize-ibus@hollowman.ml/schemas/
dconf update
