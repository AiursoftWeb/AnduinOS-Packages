#!/bin/bash

check() {
    require_binaries \
        /usr/libexec/anduinos-btrfs-snapshots-manager-initramfs \
        /usr/libexec/anduinos-btrfs-snapshots-manager-confirm \
        /usr/bin/btrfs || return 1
    return 0
}

depends() {
    echo "btrfs fs-lib"
    return 0
}

installkernel() {
    instmods btrfs
}

install() {
    inst_multiple \
        /usr/libexec/anduinos-btrfs-snapshots-manager-initramfs \
        /usr/libexec/anduinos-btrfs-snapshots-manager-confirm \
        /usr/bin/btrfs \
        cat chmod cp ln mkdir mount umount readlink blkid
    inst_simple \
        /usr/share/anduinos-btrfs-snapshots-manager/recovery-protocol-version \
        /etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version
    inst_hook pre-mount 50 "$moddir/anduinos-btrfs-snapshots-manager.sh"
}
