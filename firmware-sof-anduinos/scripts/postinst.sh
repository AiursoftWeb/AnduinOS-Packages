#!/bin/sh
set -e

SRC="/usr/share/firmware-sof-anduinos"
TMP="$(mktemp -d /tmp/anduinos-sof.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

echo "Installing Intel SOF firmware..."
tar -xzf "$SRC/sof-firmware.tar.gz" -C "$TMP"

# Author's official install.sh handles everything
(
    cd "$TMP/sof-bin-"*
    ./install.sh
)

rm -rf "$TMP"
echo "SOF firmware installed."
