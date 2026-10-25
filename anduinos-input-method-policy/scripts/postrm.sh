#!/bin/sh
set -e

case "$1" in
    remove|abort-install)
        original=/usr/share/ibus-mozc/ibus-mozc-gnome-initial-setup.sh
        if [ "$(dpkg-divert --listpackage "$original")" = anduinos-input-method-policy ]; then
            dpkg-divert --package anduinos-input-method-policy --remove --rename \
                --divert "$original.ubuntu-original" "$original"
        fi
        ;;
esac
