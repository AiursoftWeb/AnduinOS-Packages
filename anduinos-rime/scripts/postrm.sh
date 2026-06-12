#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "abort-install" ] || [ "$1" = "disappear" ]; then
    dpkg-divert --remove --package anduinos-rime --rename \
        --divert /usr/share/rime-data/default.yaml.prelude \
        /usr/share/rime-data/default.yaml
fi
#DEBHELPER#
exit 0
