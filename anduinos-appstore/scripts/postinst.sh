#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    # Keep this directory even when no Flatpak application is installed yet.
    # dbus-broker skips missing service directories when the user bus starts;
    # it does not watch their parents for directories created later by Flatpak.
    # Consequently, a newly installed DBusActivatable application (e.g. GHex)
    # appears in the start menu but cannot launch: its service is unknown to
    # the bus, although running it directly with "flatpak run" still works.
    # Creating the directory before login lets the bus watch new service files.
    # This does not refresh an already running bus; existing sessions need a
    # new login. Do not restart user buses here, as that disrupts their sessions.
    # Related upstream history: https://github.com/flatpak/flatpak/issues/3342
    install -d -m 755 /var/lib/flatpak/exports/share/dbus-1/services
    flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || \
        echo "Warning: Failed to add Flathub remote. You may need to add it manually." >&2
fi
#DEBHELPER#
exit 0
