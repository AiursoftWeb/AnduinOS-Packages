#!/bin/sh
set -e

if { [ "$1" = "remove" ] || [ "$1" = "purge" ]; } && command -v update-grub >/dev/null 2>&1; then
    update-grub
fi

#DEBHELPER#
exit 0
