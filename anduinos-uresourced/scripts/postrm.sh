#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    # The system daemon resets user allocations when it stops. Remove its
    # generated session policy as well, so removal restores default weights.
    rm -f /run/systemd/user/session.slice.d/99-uresourced.conf
    if [ -d /run/systemd/system ]; then
        deb-systemd-invoke --user daemon-reload
    fi
fi

if [ "$1" = purge ]; then
    rm -f /etc/uresourced.conf.d/90-anduinos-control-panel.conf
    rmdir --ignore-fail-on-non-empty /etc/uresourced.conf.d 2>/dev/null || true
fi
