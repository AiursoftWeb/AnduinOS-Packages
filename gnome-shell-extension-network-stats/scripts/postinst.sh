#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/network-stats@gnome.noroadsleft.xyz/schemas/
dconf update
