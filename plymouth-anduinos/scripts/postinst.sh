#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    # Depends guarantees this entry point on a normal upgrade. A damaged
    # installation must fail before changing the theme or any boot image.
    if [ ! -x /usr/libexec/anduinos-dracut-verify ]; then
        echo 'plymouth-anduinos: required initrd writer is missing; reinstall anduinos-core-system' >&2
        exit 1
    fi
    # 1. Register and set graphical splash theme
    update-alternatives --install \
        /usr/share/plymouth/themes/default.plymouth \
        default.plymouth \
        /usr/share/plymouth/themes/anduinos/anduinos.plymouth \
        150
    update-alternatives --set \
        default.plymouth \
        /usr/share/plymouth/themes/anduinos/anduinos.plymouth || true

    # 2. Register and set text fallback theme
    update-alternatives --install \
        /usr/share/plymouth/themes/text.plymouth \
        text.plymouth \
        /usr/share/plymouth/themes/anduinos-text/anduinos-text.plymouth \
        150
    update-alternatives --set \
        text.plymouth \
        /usr/share/plymouth/themes/anduinos-text/anduinos-text.plymouth || true

    # 3. Rebuild all images through AnduinOS's staged writer. Never report a
    # successful package transaction after silently losing the boot splash or
    # producing an unverified initrd.
    /usr/libexec/anduinos-dracut-verify --rebuild
fi
