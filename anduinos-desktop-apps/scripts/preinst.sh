#!/bin/sh
set -e

# Replace Celluloid's desktop Name with its GenericName.
# The upstream "Celluloid" name is confusing in many cultures — the
# GenericName (e.g. "多媒体播放器", "Multimedia Player") is universally
# understood and already provided by upstream.
ORIGINAL="/usr/share/applications/io.github.celluloid_player.Celluloid.desktop"
DIVERTED="/usr/share/applications/io.github.celluloid_player.Celluloid.desktop.ubuntu-original"

if [ install = "$1" ] || [ upgrade = "$1" ]; then
    dpkg-divert --add --package anduinos-desktop-apps --rename \
        --divert "$DIVERTED" \
        "$ORIGINAL"
fi

# Replace Geary desktop Name with localized "Email"/"Mail"
GEARY_ORIGINAL="/usr/share/applications/org.gnome.Geary.desktop"
GEARY_DIVERTED="/usr/share/applications/org.gnome.Geary.desktop.ubuntu-original"

if [ install = "$1" ] || [ upgrade = "$1" ]; then
    dpkg-divert --add --package anduinos-desktop-apps --rename \
        --divert "$GEARY_DIVERTED" \
        "$GEARY_ORIGINAL"
fi
