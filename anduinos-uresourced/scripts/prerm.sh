#!/bin/sh
set -e
if [ "$1" = remove ] && [ -d /run/systemd/system ]; then
    deb-systemd-invoke --user stop uresourced.service
fi
