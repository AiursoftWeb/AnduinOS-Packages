#!/bin/sh
set -e
if [ "$1" = configure ] && [ -d /run/systemd/system ]; then
    # Existing user managers must see the new graphical-session dependency
    # before the next login; they can survive a logout for some time.
    deb-systemd-invoke --user daemon-reload
fi
