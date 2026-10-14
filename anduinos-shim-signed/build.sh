#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
test "${1:-amd64}" = amd64
mkdir -p obj/upstream deploy
deb=obj/upstream/shim-signed.deb
expected=f8ed71ce2d91a304b6d5eb84997f846f331b554578bc02dbfe78e13ad8ac81a9
if [ ! -s "$deb" ]; then
    curl --fail --location --retry 3 --connect-timeout 30 \
      https://archive.ubuntu.com/ubuntu/pool/main/s/shim-signed/shim-signed_1.59+15.8-0ubuntu2_amd64.deb \
      -o "$deb"
fi
printf '%s  %s\n' "$expected" "$deb" | sha256sum --check
dpkg-deb --extract "$deb" deploy
# No upstream maintainer scripts are inherited. All boot writes have one owner.
find deploy/usr/lib/shim -maxdepth 1 -type f -name '*.efi*' -print0 \
    | sort -z | xargs -0 sha256sum > obj/efi-sha256.txt
mkdir -p deploy/usr/share/doc/anduinos-shim-signed
cp obj/efi-sha256.txt deploy/usr/share/doc/anduinos-shim-signed/efi-sha256.txt
printf '%s\n' "shim-signed 1.59+15.8-0ubuntu2 amd64 sha256:$expected" \
    > deploy/usr/share/doc/anduinos-shim-signed/upstream.txt
