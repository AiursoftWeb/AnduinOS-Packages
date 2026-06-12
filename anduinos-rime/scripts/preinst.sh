#!/bin/sh
set -e
if [ "$1" = "install" ] || [ "$1" = "upgrade" ]; then
    dpkg-divert --add --package anduinos-rime --rename \
        --divert /usr/share/rime-data/default.yaml.prelude \
        /usr/share/rime-data/default.yaml

    # language-selector-common may not be present on minimal installs
    dpkg-divert --add --package anduinos-rime --rename \
        --divert /usr/share/language-selector/data/pkg_depends.ubuntu \
        /usr/share/language-selector/data/pkg_depends || true
fi
#DEBHELPER#
exit 0
