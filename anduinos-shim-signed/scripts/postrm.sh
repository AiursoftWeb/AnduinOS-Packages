#!/bin/sh
set -eu
parameters=$(python3 -m anduinos_secureboot.architecture shell "$(dpkg --print-architecture)")
eval "$parameters"
if [ "$1" = remove ]; then
    update-alternatives --remove shim${BOOT_EFI_SUFFIX}.efi.signed \
        /usr/lib/shim/shim${BOOT_EFI_SUFFIX}.efi.signed.latest
fi
# Certificates and ESP files are machine state, not disposable package data.
