#!/bin/bash

check() {
    # This module is an ISO-build artifact. Installed-system host-only images
    # must never acquire Live root discovery merely because a stale package is
    # present while an installer transaction is still being finalized.
    [[ $hostonly ]] && return 1
    return 255
}

depends() {
    echo "dmsquash-live dmsquash-live-autooverlay overlayfs plymouth"
    return 0
}

install() {
    upstream="$dracutbasedir/modules.d/70dmsquash-live-autooverlay/create-overlay.sh"
    if [[ ! -x "$upstream" ]]; then
        dfatal "The upstream Dracut auto-overlay helper is unavailable: $upstream"
        return 1
    fi
    inst_script "$upstream" "/sbin/create-overlay.upstream"
    # The dependency already installed /sbin/create-overlay. dracut-install
    # does not replace an existing destination reliably, so remove exactly
    # that initrd entry before installing the AnduinOS compatibility wrapper.
    rm -f "$initdir/sbin/create-overlay"
    inst_script "$moddir/anduinos-create-overlay.sh" "/sbin/create-overlay"
    inst_hook pre-pivot 90 "$moddir/anduinos-live-prepare.sh"

    # Add a small boundary around root discovery rather than forking upstream's
    # overlay implementation. This check must precede SquashFS mounting.
    upstream="$dracutbasedir/modules.d/70dmsquash-live/dmsquash-live-root.sh"
    inst_script "$upstream" "/sbin/dmsquash-live-root.upstream"
    rm -f "$initdir/sbin/dmsquash-live-root"
    inst_script "$moddir/anduinos-live-root.sh" "/sbin/dmsquash-live-root"
    inst_script /usr/libexec/anduinos-media-check
    inst_multiple bash checkisomd5 md5sum realpath flock tail sleep sed \
        readlink cut grep stat mktemp mv chmod mkdir rm id findmnt mount umount \
        poweroff awk
    inst_dir /usr/share/anduinos-live/media-check
    local entry
    for entry in /usr/share/anduinos-live/media-check/*.txt; do
        inst_simple "$entry"
    done
    # Only the dedicated non-host-only Live image selects this theme.
    local theme=/usr/share/plymouth/themes/anduinos-media
    for entry in "$theme"/*; do inst_simple "$entry"; done
    inst_multiple "$(plymouth --get-splash-plugin-path)/script.so"
    rm -f "$initdir/usr/share/plymouth/themes/default.plymouth" \
        "$initdir/etc/alternatives/default.plymouth"
    ln -s /usr/share/plymouth/themes/anduinos-media/anduinos-media.plymouth \
        "$initdir/usr/share/plymouth/themes/default.plymouth"
    # Plymouth's normal population installs one locale's default font. Include
    # fallback fonts for all Live languages before the root filesystem exists.
    for entry in /usr/share/fonts/truetype/noto/NotoSans-Regular.ttf \
        /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
        /usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf \
        /usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf \
        /usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf; do
        [[ ! -f $entry ]] || inst_simple "$entry"
    done
}
