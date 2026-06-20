#!/bin/sh
set -e

ORIGINAL="/usr/share/applications/io.github.celluloid_player.Celluloid.desktop"
DIVERTED="/usr/share/applications/io.github.celluloid_player.Celluloid.desktop.ubuntu-original"

if [ remove = "$1" ] || [ purge = "$1" ]; then
    dpkg-divert --remove --package anduinos-desktop-apps --rename \
        --divert "$DIVERTED" \
        "$ORIGINAL" 2>/dev/null || true
fi

GEARY_ORIGINAL="/usr/share/applications/org.gnome.Geary.desktop"
GEARY_DIVERTED="/usr/share/applications/org.gnome.Geary.desktop.ubuntu-original"

if [ remove = "$1" ] || [ purge = "$1" ]; then
    dpkg-divert --remove --package anduinos-desktop-apps --rename \
        --divert "$GEARY_DIVERTED" \
        "$GEARY_ORIGINAL" 2>/dev/null || true
fi

AMBEROL_ORIGINAL="/usr/share/applications/io.bassi.Amberol.desktop"
AMBEROL_DIVERTED="/usr/share/applications/io.bassi.Amberol.desktop.ubuntu-original"

if [ remove = "$1" ] || [ purge = "$1" ]; then
    dpkg-divert --remove --package anduinos-desktop-apps --rename \
        --divert "$AMBEROL_DIVERTED" \
        "$AMBEROL_ORIGINAL" 2>/dev/null || true
fi
