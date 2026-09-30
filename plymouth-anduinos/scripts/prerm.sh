#!/bin/sh
set -e

if [ "$1" = "remove" ] || [ "$1" = "deconfigure" ]; then
    update-alternatives --remove \
        default.plymouth \
        /usr/share/plymouth/themes/anduinos/anduinos.plymouth || true

    update-alternatives --remove \
        text.plymouth \
        /usr/share/plymouth/themes/anduinos-text/anduinos-text.plymouth || true

    if [ -x /usr/libexec/anduinos-dracut-verify ]; then
        /usr/libexec/anduinos-dracut-verify --rebuild
    else
        # Allow recovery/removal when the dependency has already been lost.
        # Never guess another generator or overwrite images without validation.
        echo 'plymouth-anduinos: initrd writer unavailable during removal; existing boot images retained' >&2
    fi
fi
