#!/bin/sh
set -eu
case "$1" in
  configure)
    # Ubuntu manages this file with ucf, not dpkg conffiles. Taking it over as
    # a new conffile would prompt (or fail noninteractively) and risk replacing
    # the existing kernel command line. Initialize only genuinely new systems.
    if [ ! -e /etc/default/grub ] && [ ! -L /etc/default/grub ]; then
        install -m 0644 /usr/share/grub/default/grub /etc/default/grub
    fi
    # dpkg preserves an existing directory's mode across package upgrades.
    # Keep this package-owned signing input inaccessible to non-root writers.
    test ! -L /usr/lib/anduinos-grub
    chmod 0755 /usr/lib/anduinos-grub
    if [ -s /boot/grub/grub.cfg ] && mountpoint -q /boot/efi; then
        /usr/libexec/anduinos-grub-update prepare
    fi
    dpkg-trigger --no-await anduinos-boot-chain-update
    ;;
esac
