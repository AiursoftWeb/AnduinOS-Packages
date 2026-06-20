#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    # Remove Ubuntu session overrides to prevent interference
    rm -f /usr/share/glib-2.0/schemas/10_ubuntu-session.gschema.override 2>/dev/null || true
fi
