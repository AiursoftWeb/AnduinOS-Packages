#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ] || [ "$1" = "abort-install" ] || [ "$1" = "disappear" ]; then
    dpkg-divert --remove --package anduinos-rime --rename \
        --divert /usr/share/rime-data/default.yaml.prelude \
        /usr/share/rime-data/default.yaml || true

    dpkg-divert --remove --package anduinos-rime --rename \
        --divert /usr/share/language-selector/data/pkg_depends.ubuntu \
        /usr/share/language-selector/data/pkg_depends || true
fi
#DEBHELPER#
exit 0
