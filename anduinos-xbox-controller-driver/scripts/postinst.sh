#!/bin/sh
set -e

VERSION="v0.11-pre-63-g3acca9f"
PKG_NAME="hid-xpadneo"

case "$1" in
    configure)
        KERNEL_RELEASE="$(uname -r)"
        DKMS_ALL_STATUS="$(dkms status -m "$PKG_NAME" -v "$VERSION")"
        DKMS_KERNEL_STATUS="$(
            dkms status -m "$PKG_NAME" -v "$VERSION" -k "$KERNEL_RELEASE"
        )"
        if [ -z "$DKMS_ALL_STATUS" ]; then
            echo "Registering $PKG_NAME/$VERSION to DKMS..."
            dkms add -m "$PKG_NAME" -v "$VERSION"
        fi
        case "$DKMS_KERNEL_STATUS" in
            *": installed"*)
                echo "$PKG_NAME/$VERSION is already installed for $KERNEL_RELEASE."
                ;;
            *": built"*)
                echo "Installing $PKG_NAME/$VERSION for $KERNEL_RELEASE..."
                dkms install -m "$PKG_NAME" -v "$VERSION" -k "$KERNEL_RELEASE"
                ;;
            *)
                echo "Building $PKG_NAME/$VERSION for $KERNEL_RELEASE..."
                dkms build -m "$PKG_NAME" -v "$VERSION" -k "$KERNEL_RELEASE"
                echo "Installing $PKG_NAME/$VERSION for $KERNEL_RELEASE..."
                dkms install -m "$PKG_NAME" -v "$VERSION" -k "$KERNEL_RELEASE"
                ;;
        esac
        ;;
esac

exit 0
