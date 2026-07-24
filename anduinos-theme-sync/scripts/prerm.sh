#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
    systemctl --global disable theme-sync.service || true
fi
#DEBHELPER#
exit 0
