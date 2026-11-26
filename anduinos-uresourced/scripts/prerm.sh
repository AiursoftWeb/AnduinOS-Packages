#!/bin/sh
set -e
case "$1" in
    remove|deconfigure)
        if [ -d /run/systemd/system ]; then
            deb-systemd-invoke --user stop uresourced.service || true
        fi
        ;;
esac
