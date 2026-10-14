#!/bin/sh
set -eu
# Keep the upstream DKMS program byte-for-byte intact and updated by its own
# package; our tiny frontend runs the key check before upstream prepare_mok.
dpkg-divert --package anduinos-secureboot-toolkit --add --rename \
    --divert /usr/libexec/anduinos-dkms-original /usr/sbin/dkms
