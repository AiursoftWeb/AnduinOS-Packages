#!/bin/sh
set -e

VERSION="v0.11-pre-63-g3acca9f"
PKG_NAME="hid-xpadneo"

case "$1" in
    remove|upgrade|deconfigure)
        DKMS_STATUS="$(dkms status -m "$PKG_NAME" -v "$VERSION")"
        if [ -n "$DKMS_STATUS" ]; then
            echo "Removing $PKG_NAME/$VERSION from DKMS..."
            dkms remove -m "$PKG_NAME" -v "$VERSION" --all
        else
            echo "$PKG_NAME/$VERSION is not registered with DKMS; nothing to remove."
        fi
        ;;
esac

exit 0
