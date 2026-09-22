#!/bin/bash
# This wrapper is entered after Dracut has resolved the real Live source, but
# before dmsquash-live-root mounts SquashFS or builds the overlay.
. /lib/dracut-lib.sh

if ! getargbool 0 rd.anduinos.live; then
    exec /sbin/dmsquash-live-root.upstream "$@"
fi

mode=$(getarg rd.anduinos.media-check) || mode=auto
overlay=$(getarg rd.overlay) || overlay=
if [[ $mode != skip && ( $overlay != LABEL=ANDUINOS-PERSIST || $mode == force ) ]]; then
    state=/run/anduinos-live
    mkdir -p "$state/media"
    args=(--interactive --media "$state/media")
    [[ $mode != force ]] || args+=(--force)
    language=$(getarg locale) || language=en_US
    args+=(--locale "$language")
    livedev=$1
    # Ventoy's mapper/loop device already describes the selected ISO. Resolve
    # a parent only for an actual ISO9660 partition on a hybrid ISO9660 disk.
    checkdev=$livedev
    if [[ -b $livedev ]]; then
        realdev=$(readlink -f "$livedev")
        sysdev=/sys/class/block/${realdev##*/}
        if [[ -f $sysdev/partition ]]; then
            parent=$(readlink -f "$sysdev/..")
            parent=/dev/${parent##*/}
            if [[ $(blkid -s TYPE -o value "$parent") == iso9660 ]]; then
                checkdev=$parent
            fi
        fi
    fi
    # Read-only access to the medium, not the SquashFS root. A failed mount is
    # reported by the helper as unavailable and remains recoverable in the UI.
    mounted=0
    if mount -o ro "$livedev" "$state/media"; then
        mounted=1
        if [[ $(findmnt -n -o FSTYPE -T "$state/media") == iso9660 \
            && $overlay != LABEL=ANDUINOS-PERSIST ]]; then
            args+=(--device "$checkdev")
        fi
    fi
    check_rc=0
    /usr/libexec/anduinos-media-check "${args[@]}" \
        < /dev/console > /dev/console 2>&1 || check_rc=$?
    # The helper only returns after success or an explicit skip/continue.
    # A continuation keeps failed/unknown in the report for installer preflight.
    ((mounted == 0)) || umount "$state/media"
    if ((check_rc != 0 && check_rc != 2)); then
        warn "AnduinOS installation media verification did not reach a safe decision"
        exit "$check_rc"
    fi
fi

# Use our policy argument, NOT rd.live.check: upstream hides Plymouth and its
# failed-check branch sleeps for 12 hours before entering emergency/sulogin.
# Older manually edited command lines must not accidentally run that second
# checker. Supply the normal cmdline minus only this obsolete check argument.
ANDUINOS_LIVE_CMDLINE=" $(getcmdline) "
ANDUINOS_LIVE_CMDLINE=$(printf '%s\n' "$ANDUINOS_LIVE_CMDLINE" | sed -E 's/(^|[[:space:]])rd\.live\.check(=[^[:space:]]*)?([[:space:]]|$)/ /g')
# getarg re-reads getcmdline on every call, so exporting CMDLINE alone would
# NOT suppress the legacy checker. Source upstream with this narrow override.
getcmdline() { printf '%s' "$ANDUINOS_LIVE_CMDLINE"; }
. /sbin/dmsquash-live-root.upstream
