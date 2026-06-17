#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/dash-to-panel@jderose9.github.com/schemas/
dconf update
