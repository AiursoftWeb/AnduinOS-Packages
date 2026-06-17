#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/blur-my-shell@aunetx/schemas/
dconf update
