#!/bin/sh
set -e

VERSION="v0.11-pre-86-g547eb90"
PKG_NAME="hid-xpadneo"

case "$1" in
    failed-upgrade)
        # The old prerm did not finish. Its source must remain available for
        # repair; the new script cannot infer which old DKMS version to remove.
        echo "The previous xpadneo driver could not be removed from DKMS. Restore DKMS availability and retry the upgrade." >&2
        exit 1
        ;;
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
