#!/bin/sh
set -e

VERSION="v0.11-pre-86-g547eb90"
PKG_NAME="hid-xpadneo"

case "$1" in
    configure)
        DKMS_ALL_STATUS="$(dkms status -m "$PKG_NAME" -v "$VERSION")"
        if [ -z "$DKMS_ALL_STATUS" ]; then
            echo "Registering $PKG_NAME/$VERSION to DKMS..."
            dkms add -m "$PKG_NAME" -v "$VERSION"
        fi
        # prerm removes this driver from every kernel. Rebuild every installed
        # kernel with headers, including a newer kernel not yet booted. Do not
        # use uname: in a chroot it describes the host, not the target system.
        for KERNEL_DIR in /lib/modules/*; do
            [ -d "$KERNEL_DIR" ] || continue
            KERNEL_RELEASE=${KERNEL_DIR##*/}
            if [ ! -d "$KERNEL_DIR/build/include" ]; then
                echo "Skipping $KERNEL_RELEASE: kernel headers are not installed."
                continue
            fi
            DKMS_KERNEL_STATUS="$(
                dkms status -m "$PKG_NAME" -v "$VERSION" -k "$KERNEL_RELEASE"
            )"
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
        done
        ;;
esac

exit 0
