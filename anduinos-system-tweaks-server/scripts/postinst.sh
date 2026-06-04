#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    sysctl --system >/dev/null 2>&1 || true
fi
#DEBHELPER#
exit 0
