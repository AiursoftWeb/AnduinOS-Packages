#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    # Register AnduinOS as the system's default X/Wayland session manager with high priority
    update-alternatives --install /usr/bin/x-session-manager x-session-manager /usr/bin/gnome-session 60
    
    # Remove Ubuntu session overrides to prevent interference
    rm -f /usr/share/glib-2.0/schemas/10_ubuntu-session.gschema.override 2>/dev/null || true
    glib-compile-schemas /usr/share/glib-2.0/schemas/ || true
fi

#DEBHELPER#
exit 0
