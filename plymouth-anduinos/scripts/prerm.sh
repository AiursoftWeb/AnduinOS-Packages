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
        dracut --force --regenerate-all
    fi
fi
