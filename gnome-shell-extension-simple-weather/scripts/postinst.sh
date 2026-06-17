#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/simple-weather@romanlefler.com/schemas/
dconf update
