#!/bin/sh
set -e

case "$1" in
    install|upgrade)
        dpkg-divert --package anduinos-input-method-policy --add --rename \
            --divert /usr/share/ibus-mozc/ibus-mozc-gnome-initial-setup.sh.ubuntu-original \
            /usr/share/ibus-mozc/ibus-mozc-gnome-initial-setup.sh
        ;;
esac
