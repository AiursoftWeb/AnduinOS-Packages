#!/bin/sh
set -eu
if [ "$1" = remove ]; then
    update-alternatives --remove shimx64.efi.signed \
        /usr/lib/shim/shimx64.efi.signed.latest
fi
# Certificates and ESP files are machine state, not disposable package data.
