#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
    systemctl --global disable deskmon.service || true
fi
#DEBHELPER#
exit 0
