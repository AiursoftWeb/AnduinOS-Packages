#!/bin/sh
set -e

if [ "$1" = "remove" ]; then
    rm -f /etc/systemd/user/graphical-session.target.wants/deskmon.service
fi

#DEBHELPER#
exit 0
