#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "deconfigure" ]; then
    systemctl --global disable anduinos-theme-schedule.service || true
fi
#DEBHELPER#
exit 0
