#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    mkdir -p /etc/systemd/user/graphical-session.target.wants
    ln -sf /usr/lib/systemd/user/deskmon.service /etc/systemd/user/graphical-session.target.wants/deskmon.service

    # Notify running user instances to pick up the new unit
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload >/dev/null 2>&1 || true
        systemctl --user try-restart deskmon.service >/dev/null 2>&1 || true
    fi
fi

#DEBHELPER#
exit 0
