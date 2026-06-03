#!/bin/sh
set -e
chmod 755 /usr/share/gnome-shell/extensions/gtk4-ding@smedius.gitlab.com/app/adw-ding.js
glib-compile-schemas /usr/share/gnome-shell/extensions/gtk4-ding@smedius.gitlab.com/schemas/
