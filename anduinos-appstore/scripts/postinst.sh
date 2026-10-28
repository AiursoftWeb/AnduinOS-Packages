#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    # dbus-broker only watches service directories that exist at session startup.
    # Seed this before login so the first installed Flatpak can be D-Bus activated.
    install -d -m 755 /var/lib/flatpak/exports/share/dbus-1/services
    flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || \
        echo "Warning: Failed to add Flathub remote. You may need to add it manually." >&2
fi
#DEBHELPER#
exit 0
