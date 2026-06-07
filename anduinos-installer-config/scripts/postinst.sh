#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    dpkg-divert --add --package anduinos-installer-config --rename \
        --divert /usr/lib/ubiquity/localechooser/languagelist.ubuntu \
        /usr/lib/ubiquity/localechooser/languagelist

    dpkg-divert --add --package anduinos-installer-config --rename \
        --divert /usr/lib/ubiquity/localechooser/languagelist.data.gz.ubuntu \
        /usr/lib/ubiquity/localechooser/languagelist.data.gz

    dpkg-divert --add --package anduinos-installer-config --rename \
        --divert /usr/share/applications/ubiquity.desktop.ubuntu \
        /usr/share/applications/ubiquity.desktop

    cp /usr/share/anduinos-installer-config/languagelist \
       /usr/lib/ubiquity/localechooser/languagelist

    gzip -nc /usr/share/anduinos-installer-config/languagelist.data \
        > /usr/lib/ubiquity/localechooser/languagelist.data.gz

    sed "s%^Exec=.*%Exec=/usr/bin/anduinos-installer%" \
        /usr/share/applications/ubiquity.desktop.ubuntu \
        > /usr/share/applications/ubiquity.desktop
fi

#DEBHELPER#
exit 0
