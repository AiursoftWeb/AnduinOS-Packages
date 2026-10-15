#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
: "${APKG_STAGE_DIR:?Run apkg build to select the upstream package}"
target=${APKG_STAGE_DIR##*/}
parameters=$(python3 -I ../anduinos-secureboot-toolkit/src/anduinos_secureboot/architecture.py shell "${target##*_}")
eval "$parameters"

# Keep only the official signed chain and revocation check. Ubuntu's kernel
# hooks and update-secureboot-policy must not create a second signing identity.
payload=$(mktemp -d)
trap 'rm -rf "$payload"' EXIT
mkdir -p "$payload/usr/lib/shim" "$payload/usr/share/doc/anduinos-shim-signed"
for file in "shim$BOOT_EFI_SUFFIX.efi.signed.latest" "mm$BOOT_EFI_SUFFIX.efi" is-not-revoked; do
    test -s "$APKG_STAGE_DIR/usr/lib/shim/$file"
    cp -a "$APKG_STAGE_DIR/usr/lib/shim/$file" "$payload/usr/lib/shim/"
done
mv "$APKG_STAGE_DIR/usr/share/doc/shim-signed" "$payload/usr/share/doc/anduinos-shim-signed/upstream"
rm -rf "$APKG_STAGE_DIR/usr" "$APKG_STAGE_DIR/etc"
mv "$payload/usr" "$APKG_STAGE_DIR/"
