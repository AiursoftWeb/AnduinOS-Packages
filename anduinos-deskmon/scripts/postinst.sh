#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    systemctl --global enable deskmon.service || true
fi
#DEBHELPER#
exit 0
