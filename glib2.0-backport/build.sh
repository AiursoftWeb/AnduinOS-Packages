#!/bin/bash
# Build the original Ubuntu source package; do not merge its binary packages.
set -euo pipefail
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
. /etc/os-release
if [ "${VERSION_CODENAME:-}" != stonking ]; then
    echo 'This temporary GLib backport is only for Stonking.' >&2
    exit 1
fi
case $(dpkg --print-architecture) in
    amd64|arm64) ;;
    *) echo 'Build on native amd64 or arm64.' >&2; exit 1 ;;
esac
if [ "$#" != 1 ] || [ -e "$1" ]; then
    echo 'Usage: bash build.sh NEW_OUTPUT_DIRECTORY' >&2
    exit 1
fi

base_version=2.90.0-1
version=$(dpkg-parsechangelog -l"$here/changelog" -SVersion)
test "$(dpkg-parsechangelog -l"$here/changelog" -SDistribution)" = stonking
dpkg --compare-versions "$base_version" lt "$version"
for successor in 2.90.0-1build1 2.90.0-1ubuntu0.1 2.90.0-2 2.90.1-1; do
    dpkg --compare-versions "$version" lt "$successor"
done

# Use signed Ubuntu deb-src indexes refreshed by the build environment. A new
# Ubuntu source revision requires review, rather than silently freezing it out.
latest=$base_version
while read -r available; do
    if dpkg --compare-versions "$available" gt "$latest"; then
        latest=$available
    fi
done < <(apt-cache showsrc --only-source glib2.0 | sed -n 's/^Version: //p')
if [ "$latest" != "$base_version" ]; then
    echo "Ubuntu now offers glib2.0 $latest; review official fixes before rebuilding this backport." >&2
    exit 1
fi

mkdir -p "$1"
cd "$1"
mkdir evidence
apt-cache showsrc --only-source glib2.0 > evidence/ubuntu-source-record.txt
apt-get source --download-only "glib2.0=$base_version"
sha256sum glib2.0* > evidence/ubuntu-source-sha256.txt
dpkg-source -x "glib2.0_${base_version}.dsc" source
cd source

while read -r name; do
    test -n "$name"
    install -m644 "$here/patches/$name" "debian/patches/$name"
    printf '%s\n' "$name" >> debian/patches/series
done < "$here/patches/series"
cat "$here/changelog" debian/changelog > debian/changelog.new
mv debian/changelog.new debian/changelog
dpkg-source --before-build .
dpkg-checkbuilddeps

# Keep the complete Debian build, maintainer scripts, ABI metadata, copyright,
# source artifacts and upstream tests. No nocheck or replacement .so payload.
dpkg-buildpackage -us -uc 2>&1 | tee ../evidence/build.log
cd ..
sha256sum ./*.deb > evidence/binary-sha256.txt
for deb in ./*.deb; do
    test "$(dpkg-deb -f "$deb" Version)" = "$version"
    dpkg-deb -f "$deb" Package Source Version Architecture
done > evidence/binary-control.txt
printf 'Built GLib %s for Stonking; nothing has been uploaded.\n' "$version"
