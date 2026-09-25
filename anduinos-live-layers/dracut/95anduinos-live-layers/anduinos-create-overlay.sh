#!/bin/sh

command -v getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh

unsupported_togo_media() {
    message='AnduinOS To Go requires a USB drive written in DD mode with unallocated space after the image. This boot medium is not supported.'
    splash_message='To Go needs a DD-written USB drive with free space. This medium is unsupported.'
    warn "$message"
    printf '%s\n' "$message" >&2
    printf '\n%s\n' "$message" > /dev/console 2>/dev/null || :
    if plymouth --ping >/dev/null 2>&1; then
        plymouth display-message --text="$splash_message" >/dev/null 2>&1 || :
        sleep 15
        plymouth quit >/dev/null 2>&1 || :
    fi
    die "$message"
    return 1
}

prepare_togo_media() {
    overlay="$(getarg rd.overlay)"
    [ "$overlay" = "LABEL=ANDUINOS-PERSIST" ] || return 0
    [ -n "$1" ] || { unsupported_togo_media; return 1; }

    root_device="$(readlink -f "$1")" || { unsupported_togo_media; return 1; }
    root_sysfs="/sys/class/block/${root_device##*/}"
    [ -f "$root_sysfs/partition" ] || { unsupported_togo_media; return 1; }
    read -r partition < "$root_sysfs/partition" || { unsupported_togo_media; return 1; }
    read -r readonly < "$root_sysfs/ro" || { unsupported_togo_media; return 1; }
    [ "$partition" = 1 ] && [ "$readonly" = 0 ] \
        || { unsupported_togo_media; return 1; }
    [ "$(blkid -s TYPE -o value "$root_device" 2>/dev/null)" = iso9660 ] \
        || { unsupported_togo_media; return 1; }

    parent_sysfs="$(readlink -f "$root_sysfs/..")" \
        || { unsupported_togo_media; return 1; }
    block_device="/dev/${parent_sysfs##*/}"
    partition_table="$(blkid -s PTTYPE -o value "$block_device" 2>/dev/null || true)"
    # Our xorriso hybrid ISO normally has an MBR (PTTYPE=dos). Other hybrid
    # layouts can use GPT; only that case needs backup-header expansion.
    case "$partition_table" in
        dos) return 0 ;;
        gpt) ;;
        *) unsupported_togo_media; return 1 ;;
    esac

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

prepare_togo_media "$1" || exit 1
if [ "$(getarg rd.overlay)" != LABEL=ANDUINOS-PERSIST ]; then
    exec /sbin/create-overlay.upstream "$@"
fi
# Upstream returns success when it skips a USB with no usable free space.
# To Go must not silently continue without the persistent partition.
/sbin/create-overlay.upstream "$@" || { unsupported_togo_media; exit 1; }
overlay_device="$(readlink -f /dev/disk/by-label/ANDUINOS-PERSIST)" \
    || { unsupported_togo_media; exit 1; }
[ -b "$overlay_device" ] || { unsupported_togo_media; exit 1; }
overlay_sysfs="/sys/class/block/${overlay_device##*/}"
[ -f "$overlay_sysfs/partition" ] || { unsupported_togo_media; exit 1; }
overlay_parent_sysfs="$(readlink -f "$overlay_sysfs/..")" \
    || { unsupported_togo_media; exit 1; }
# An unrelated drive can have the same label. Never persist onto that drive.
[ "$overlay_parent_sysfs" = "$parent_sysfs" ] \
    || { unsupported_togo_media; exit 1; }
