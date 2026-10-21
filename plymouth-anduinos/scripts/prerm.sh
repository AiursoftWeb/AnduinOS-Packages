#!/bin/sh
set -e

if [ "$1" = "remove" ] || [ "$1" = "deconfigure" ]; then
    update-alternatives --remove \
        default.plymouth \
        /usr/share/plymouth/themes/anduinos/anduinos.plymouth || true

    update-alternatives --remove \
        text.plymouth \
        /usr/share/plymouth/themes/anduinos-text/anduinos-text.plymouth || true

    if command -v update-initramfs >/dev/null 2>&1; then
        update-initramfs -u
    else
        # Allow recovery/removal when the dependency has already been lost.
        echo 'plymouth-anduinos: update-initramfs unavailable during removal; existing boot images retained' >&2
    fi
fi
