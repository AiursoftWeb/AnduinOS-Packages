#!/bin/sh
set -e

VERSION="v0.11-pre-63-g3acca9f"
PKG_NAME="hid-xpadneo"

case "$1" in
    configure)
        echo "Registering $PKG_NAME/$VERSION to DKMS..."
        dkms add -m "$PKG_NAME" -v "$VERSION" || true
        echo "Building $PKG_NAME/$VERSION via DKMS..."
        dkms build -m "$PKG_NAME" -v "$VERSION" || true
        echo "Installing $PKG_NAME/$VERSION via DKMS..."
        dkms install -m "$PKG_NAME" -v "$VERSION" || true
        ;;
esac

exit 0
