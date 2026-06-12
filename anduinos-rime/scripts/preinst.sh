#!/bin/sh
set -e
if [ "$1" = "install" ] || [ "$1" = "upgrade" ]; then
    dpkg-divert --add --package anduinos-rime --rename \
        --divert /usr/share/rime-data/default.yaml.prelude \
        /usr/share/rime-data/default.yaml
fi
#DEBHELPER#
exit 0
