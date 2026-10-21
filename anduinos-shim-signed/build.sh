#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
: "${APKG_INPUTS_DIR:?Build with apkg and BuildInput support}"
: "${APKG_STAGE_DIR:?Build with apkg}"
payload=$APKG_INPUTS_DIR/extracted/shim-signed
debs=("$APKG_INPUTS_DIR"/debs/shim-signed/*.deb)
case "$(dpkg-deb -f "${debs[0]}" Architecture)" in
    amd64) suffix=x64 ;;
    arm64) suffix=aa64 ;;
    *) echo 'Unsupported shim architecture' >&2; exit 1 ;;
esac
dest=$APKG_STAGE_DIR/usr/lib/shim
doc=$APKG_STAGE_DIR/usr/share/doc/anduinos-shim-signed
mkdir -p "$dest" "$doc/upstream"
# Select the same files on either architecture. Copy bytes, never sign or run
# them. The toolkit remains the only owner of ESP deployment.
for file in "shim$suffix.efi.signed.latest" "mm$suffix.efi" is-not-revoked; do
    cp -L --preserve=mode "$payload/usr/lib/shim/$file" "$dest/$file"
    cmp "$payload/usr/lib/shim/$file" "$dest/$file"
done
cp -a "$payload/usr/share/doc/shim-signed/." "$doc/upstream/"
cp "$payload/usr/share/doc/shim-signed/copyright" "$doc/copyright"
cp "$APKG_INPUTS_DIR/manifest.json" "$doc/"
(cd "$APKG_STAGE_DIR" && sha256sum "usr/lib/shim/shim$suffix.efi.signed.latest" "usr/lib/shim/mm$suffix.efi") > "$doc/efi-sha256.txt"
