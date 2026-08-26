#!/bin/sh

command -v getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh

getargbool 0 rd.anduinos.live || return 0

live_dir="$(getarg rd.live.dir)"
[ -n "$live_dir" ] || live_dir=LiveOS
squash_image="$(getarg rd.live.squashimg)"
[ -n "$squash_image" ] || squash_image=rootfs.squashfs

case "$live_dir" in
    /*|*..*|*[!A-Za-z0-9_./-]*)
        die "Invalid AnduinOS Live directory: $live_dir"
        return 1
        ;;
esac
case "$squash_image" in
    */*|*..*|*[!A-Za-z0-9_.-]*)
        die "Invalid AnduinOS Live image name: $squash_image"
        return 1
        ;;
esac

media_root=/run/initramfs/live
source_image="$media_root/$live_dir/$squash_image"
if [ ! -d "$media_root" ]; then
    die "AnduinOS Live media is unavailable at $media_root"
    return 1
fi
if [ ! -f "$source_image" ]; then
    die "AnduinOS Live root image is unavailable at $source_image"
    return 1
fi

runtime_root=/run/anduinos-live
mkdir -p "$NEWROOT/cdrom" "$runtime_root"
mount --bind "$media_root" "$NEWROOT/cdrom" || {
    die "AnduinOS could not preserve the Live media at /cdrom"
    return 1
}

# Dracut carries its /run mount across switch_root. Put the runtime contract on
# that shared mount so it cannot be hidden when initrd /run replaces newroot's
# initially empty /run directory during the pivot.
: > "$runtime_root/rootfs.squashfs"
mount --bind "$source_image" "$runtime_root/rootfs.squashfs" || {
    die "AnduinOS could not expose the Live root image to the installer"
    return 1
}

cat > "$runtime_root/environment" <<EOF
ANDUINOS_LIVE=1
ANDUINOS_LIVE_MEDIA=/cdrom
ANDUINOS_LIVE_SOURCE=/run/anduinos-live/rootfs.squashfs
ANDUINOS_LIVE_DIRECTORY=$live_dir
ANDUINOS_LIVE_IMAGE=$squash_image
EOF

info "AnduinOS Live media and installer source contracts are ready"
