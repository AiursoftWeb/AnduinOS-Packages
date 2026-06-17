#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/arcmenu@arcmenu.com/schemas/
dconf update
