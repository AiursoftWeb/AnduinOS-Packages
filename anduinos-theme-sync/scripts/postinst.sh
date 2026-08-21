#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    systemctl --global enable theme-sync.service || true
fi
#DEBHELPER#
exit 0
