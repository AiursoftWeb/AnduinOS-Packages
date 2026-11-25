#!/bin/sh
set -e
if [ "$1" = configure ] && [ -d /run/systemd/system ]; then
    # Existing user managers must see the new graphical-session dependency
    # before the next login; they can survive a logout for some time.
    deb-systemd-invoke --user daemon-reload
    systemctl daemon-reload
    # Preserve administrator masks/invalid custom settings without blocking
    # package configuration. The settings UI reports the actual service state.
    if ! /usr/lib/anduinos-uresourced/helper apply; then
        echo "Desktop resource settings were preserved, but could not be fully applied. Check Resource Allocation Settings or the journal." >&2
    fi
fi
