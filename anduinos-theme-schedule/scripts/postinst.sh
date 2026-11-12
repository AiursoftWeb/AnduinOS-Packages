#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    glib-compile-schemas /usr/share/glib-2.0/schemas >/dev/null 2>&1 || true
fi
#DEBHELPER#
exit 0
