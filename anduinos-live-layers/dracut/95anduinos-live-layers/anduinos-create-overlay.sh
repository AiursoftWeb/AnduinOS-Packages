#!/bin/sh

command -v getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh

fix_expanded_gpt() {
    getargbool 0 rd.anduinos.live || return 0

    overlay="$(getarg rd.overlay)"
    [ "$overlay" = "LABEL=ANDUINOS-PERSIST" ] || return 0
    [ -n "$1" ] || return 1

    root_device="$(readlink -f "$1")"
    root_sysfs="/sys/class/block/${root_device##*/}"
    [ -f "$root_sysfs/partition" ] || return 0
    read -r partition < "$root_sysfs/partition"
    read -r readonly < "$root_sysfs/ro"
    [ "$partition" = 1 ] || return 0
    [ "$readonly" = 0 ] || return 0

    parent_sysfs="$(readlink -f "$root_sysfs/..")"
    block_device="/dev/${parent_sysfs##*/}"
    partition_table="$(blkid -s PTTYPE -o value "$block_device" 2>/dev/null || true)"
    [ "$partition_table" = gpt ] || return 0

    info "Expanding the AnduinOS persistent-media backup GPT on $block_device"
    if ! parted --script --fix "$block_device" print >/dev/null; then
        die "AnduinOS could not expand the persistent-media GPT on $block_device"
        return 1
    fi
    blockdev --rereadpt "$block_device" || {
        die "AnduinOS could not reload the persistent-media GPT on $block_device"
        return 1
    }
    udevsettle
}

fix_expanded_gpt "$1" || exit 1
exec /sbin/create-overlay.upstream "$@"
