#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
: "${APKG_STAGE_DIR:?Run apkg build to select the target and upstream package}"
target=${APKG_STAGE_DIR##*/}
suite=${target%_*}
arch=${target##*_}
suite=${suite%-addon}
[[ "$suite" =~ ^[a-z][a-z0-9-]*$ ]] || { echo 'A target Ubuntu suite is required' >&2; exit 1; }
mapping=../anduinos-secureboot-toolkit/src/anduinos_secureboot
parameters=$(python3 -I "$mapping/architecture.py" shell "$arch")
eval "$parameters"
# Build the source corresponding to Apkg's selected binary package, so the
# inherited runtime dependencies and package version describe the same input.
source_version=$(gzip -cd "$APKG_STAGE_DIR/usr/share/doc/grub2-common/changelog.Debian.gz" | sed -n '1s/.*(\(.*\)).*/\1/p')
test -n "$source_version"
output="deploy/$suite/$arch"
# A newer upstream package may remove files; never merge it into old exports.
rm -rf "$output"
# Always refresh APT indexes and the base image. Docker may reuse compilation
# only when the newly downloaded source and the build recipe are unchanged.
docker buildx build --pull --platform "linux/$arch" --build-arg "SUITE=$suite" --build-arg "GRUB_VERSION=$source_version" \
    --build-context "boot-architecture=$mapping" --no-cache-filter upstream \
    --target artifacts --output "type=local,dest=$output" .
install -m 0755 scripts/grub-multi-install "$output/usr/lib/grub/grub-multi-install"
# Debian's debhelper permission and link stages are part of the payload,
# not optional packaging cosmetics: update-grub must remain executable.
for executable in usr/sbin/update-grub usr/sbin/grub-install usr/bin/grub-mkimage etc/grub.d/05_debian_theme; do
    test -x "$output/$executable"
done
for module in btrfs luks2 argon2; do
    test -s "$output/usr/lib/grub/$BOOT_GRUB_PLATFORM/$module.mod"
done
test -s "$output/usr/lib/systemd/system/grub2-common.service"
test -s "$output/usr/share/grub/default/grub"
if grep -Eq '@(GRUB|DEFAULT)_' "$output/usr/share/grub/default/grub"; then
    echo 'Unexpanded GRUB defaults template' >&2
    exit 1
fi

ln -sfn "$suite/$arch" deploy/current
# Discard the upstream binary payload; only its metadata is inherited.
rm -rf "$APKG_STAGE_DIR/usr" "$APKG_STAGE_DIR/etc"
cp -a "$output/usr" "$output/etc" "$APKG_STAGE_DIR/"
