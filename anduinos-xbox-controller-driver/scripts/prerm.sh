#!/bin/sh
set -e

VERSION="v0.11-pre-63-g3acca9f"
PKG_NAME="hid-xpadneo"

case "$1" in
    remove|upgrade|deconfigure)
        echo "Removing $PKG_NAME/$VERSION from DKMS..."
        dkms remove -m "$PKG_NAME" -v "$VERSION" --all || true
        ;;
esac

exit 0
