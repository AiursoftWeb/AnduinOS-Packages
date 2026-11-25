#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    rm -f /run/systemd/user/session.slice.d/99-uresourced.conf
    if [ -d /run/systemd/system ]; then
        deb-systemd-invoke --user daemon-reload
    fi
fi
# dpkg manages /etc/uresourced.conf: remove preserves it, purge removes it.
