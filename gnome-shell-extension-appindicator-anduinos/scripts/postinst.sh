#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/appindicatorsupport@rgcjonas.gmail.com/schemas/
dconf update
