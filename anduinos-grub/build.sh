#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
arch=${1:-amd64}
if [ "$arch" != amd64 ]; then
    echo "AnduinOS GRUB is currently qualified for amd64 only" >&2
    exit 1
fi
# A manually exported Docker build can also seed local builds without granting
# the unprivileged Apkg process access to the Docker daemon.
recipe=$(sha256sum Dockerfile sources.sha256 sbat.csv bootstrap.cfg | sha256sum | cut -d' ' -f1)
if [ ! -s deploy/usr/lib/anduinos-grub/grubx64.efi ] || \
   [ "$(cat deploy/.recipe 2>/dev/null || true)" != "$recipe" ]; then
    docker build --target artifacts --output type=local,dest=deploy .
    printf '%s\n' "$recipe" > deploy/.recipe
fi
install -m 0755 scripts/grub-multi-install deploy/usr/lib/grub/grub-multi-install
# Debian's debhelper permission and link stages are part of the payload,
# not optional packaging cosmetics: update-grub must remain executable.
for executable in usr/sbin/update-grub usr/sbin/grub-install usr/bin/grub-mkimage etc/grub.d/05_debian_theme; do
    test -x "deploy/$executable"
done
for module in btrfs luks2 argon2; do
    test -s "deploy/usr/lib/grub/x86_64-efi/$module.mod"
done
test -s deploy/usr/lib/systemd/system/grub2-common.service
test -s deploy/usr/share/grub/default/grub
if grep -Eq '@(GRUB|DEFAULT)_' deploy/usr/share/grub/default/grub; then
    echo 'Unexpanded GRUB defaults template' >&2
    exit 1
fi
