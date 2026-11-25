#!/bin/sh
set -e
# Move only the unchanged old packaged default out of the override layer.
# A locally edited file remains a live administrator override. Leaving this
# conffile obsolete in dpkg preserves standard remove/purge behavior.
if [ "$1" = upgrade ] && [ -f /etc/uresourced.conf ]; then
    packaged=$(dpkg-query -W -f='${Conffiles}\n' anduinos-uresourced 2>/dev/null | awk '$1 == "/etc/uresourced.conf" { print $2 }')
    if [ -n "$packaged" ] && [ "$(md5sum /etc/uresourced.conf | cut -d' ' -f1)" = "$packaged" ]; then
        rm /etc/uresourced.conf
    fi
fi
