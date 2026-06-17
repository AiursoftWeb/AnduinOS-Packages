#!/bin/sh
set -e
glib-compile-schemas /usr/share/gnome-shell/extensions/clipboard-indicator@tudmotu.com/schemas/
dconf update
