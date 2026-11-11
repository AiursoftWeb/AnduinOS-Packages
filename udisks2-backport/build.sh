#!/bin/bash
# Rebuild the complete Ubuntu source package with one provider synchronization fix.
set -euo pipefail
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
. /etc/os-release
case ${VERSION_CODENAME:-} in
    resolute)
        base_version=2.10.91-1ubuntu2.1
        successors=(2.10.91-1ubuntu2.2 2.10.91-1ubuntu3 2.11.0-1)
        ;;
    stonking)
        base_version=2.11.2-1ubuntu1
        successors=(2.11.2-1ubuntu1.1 2.11.2-1ubuntu2 2.11.3-1)
        ;;
    *) echo 'This temporary UDisks2 backport supports Resolute and Stonking.' >&2; exit 1 ;;
esac
case $(dpkg --print-architecture) in
    amd64|arm64) ;;
    *) echo 'Build on native amd64 or arm64.' >&2; exit 1 ;;
esac
if [ "$#" != 1 ] || [ -e "$1" ]; then
    echo 'Usage: bash build.sh NEW_OUTPUT_DIRECTORY' >&2
    exit 1
fi

version=$(dpkg-parsechangelog -l"$here/changelog.$VERSION_CODENAME" -SVersion)
test "$(dpkg-parsechangelog -l"$here/changelog.$VERSION_CODENAME" -SDistribution)" = "$VERSION_CODENAME"
dpkg --compare-versions "$base_version" lt "$version"
for successor in "${successors[@]}"; do
    dpkg --compare-versions "$version" lt "$successor"
done

# Use refreshed, signed Ubuntu source indexes. Re-review newer Ubuntu revisions
# instead of silently replacing their changes with an older temporary build.
records=$(apt-cache showsrc --only-source udisks2)
printf '%s\n' "$records" | grep -Fx "Version: $base_version" > /dev/null
latest=$base_version
while read -r available; do
    if dpkg --compare-versions "$available" gt "$latest"; then
        latest=$available
    fi
done < <(printf '%s\n' "$records" | sed -n 's/^Version: //p')
if [ "$latest" != "$base_version" ]; then
    echo "Ubuntu now offers udisks2 $latest; review official fixes before rebuilding this backport." >&2
    exit 1
fi

mkdir -p "$1"
cd "$1"
mkdir evidence
printf '%s\n' "$records" > evidence/ubuntu-source-record.txt
apt-get source --download-only "udisks2=$base_version"
sha256sum udisks2* > evidence/ubuntu-source-sha256.txt
dpkg-source -x "udisks2_${base_version}.dsc" source
cd source

while read -r name; do
    test -n "$name"
    install -m644 "$here/patches/$name" "debian/patches/$name"
    printf '%s\n' "$name" >> debian/patches/series
done < "$here/patches/series"
cat "$here/changelog.$VERSION_CODENAME" debian/changelog > debian/changelog.new
mv debian/changelog.new debian/changelog
dpkg-source --before-build .
dpkg-checkbuilddeps

# Preserve Ubuntu's full binary set, security patches, maintainer scripts,
# dependencies, documentation, source artifacts and upstream build checks.
dpkg-buildpackage -us -uc 2>&1 | tee ../evidence/build.log
cd ..
sha256sum ./*.deb > evidence/binary-sha256.txt
for deb in ./*.deb; do
    test "$(dpkg-deb -f "$deb" Version)" = "$version"
    dpkg-deb -f "$deb" Package Source Version Architecture
done > evidence/binary-control.txt
printf 'Built UDisks2 %s for %s; nothing has been uploaded.\n' "$version" "$VERSION_CODENAME"
