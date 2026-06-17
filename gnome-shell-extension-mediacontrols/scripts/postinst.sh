#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/mediacontrols@cliffniff.github.com/schemas/
dconf update
