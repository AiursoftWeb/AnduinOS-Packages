#!/bin/sh
set -eu
case "$1" in
  remove|abort-install)
    if [ "$(readlink /usr/sbin/dkms 2>/dev/null || true)" = /usr/libexec/anduinos-dkms-wrapper ]; then
        rm /usr/sbin/dkms
    fi
    dpkg-divert --package anduinos-secureboot-toolkit --remove --rename \
        --divert /usr/libexec/anduinos-dkms-original /usr/sbin/dkms
    ;;
esac
