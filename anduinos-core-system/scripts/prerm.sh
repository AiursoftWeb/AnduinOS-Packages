set -eu

UPDATE_INITRAMFS=${ANDUINOS_MIGRATION_UPDATE_INITRAMFS:-${DPKG_ROOT:-}/usr/sbin/update-initramfs}
UPDATE_INITRAMFS_DIVERT=${ANDUINOS_MIGRATION_UPDATE_INITRAMFS_DIVERT:-${UPDATE_INITRAMFS}.anduinos-dracut}
UPDATE_INITRAMFS_WRAPPER=${ANDUINOS_MIGRATION_UPDATE_INITRAMFS_WRAPPER:-${DPKG_ROOT:-}/usr/libexec/anduinos-update-initramfs}
DPKG_DIVERT=${ANDUINOS_MIGRATION_DPKG_DIVERT:-dpkg-divert}
UPDATE_GRUB=${ANDUINOS_MIGRATION_UPDATE_GRUB:-${DPKG_ROOT:-}/usr/sbin/update-grub}
UPDATE_GRUB_DIVERT=${ANDUINOS_MIGRATION_UPDATE_GRUB_DIVERT:-${UPDATE_GRUB}.anduinos-grub}
UPDATE_GRUB_WRAPPER=${ANDUINOS_MIGRATION_UPDATE_GRUB_WRAPPER:-${DPKG_ROOT:-}/usr/libexec/anduinos-update-grub}
ROOT_PREFIX=${DPKG_ROOT:-}

UPDATE_INITRAMFS_DPKG_PATH=$UPDATE_INITRAMFS
UPDATE_INITRAMFS_DIVERT_DPKG_PATH=$UPDATE_INITRAMFS_DIVERT
UPDATE_INITRAMFS_LINK_TARGET=$UPDATE_INITRAMFS_WRAPPER
UPDATE_GRUB_DPKG_PATH=$UPDATE_GRUB
UPDATE_GRUB_DIVERT_DPKG_PATH=$UPDATE_GRUB_DIVERT
UPDATE_GRUB_LINK_TARGET=$UPDATE_GRUB_WRAPPER
if [ -n "$ROOT_PREFIX" ]; then
    [ "${ANDUINOS_MIGRATION_UPDATE_INITRAMFS+x}" = x ] \
        || UPDATE_INITRAMFS_DPKG_PATH=/usr/sbin/update-initramfs
    [ "${ANDUINOS_MIGRATION_UPDATE_INITRAMFS_DIVERT+x}" = x ] \
        || UPDATE_INITRAMFS_DIVERT_DPKG_PATH=/usr/sbin/update-initramfs.anduinos-dracut
    [ "${ANDUINOS_MIGRATION_UPDATE_INITRAMFS_WRAPPER+x}" = x ] \
        || UPDATE_INITRAMFS_LINK_TARGET=/usr/libexec/anduinos-update-initramfs
    [ "${ANDUINOS_MIGRATION_UPDATE_GRUB+x}" = x ] \
        || UPDATE_GRUB_DPKG_PATH=/usr/sbin/update-grub
    [ "${ANDUINOS_MIGRATION_UPDATE_GRUB_DIVERT+x}" = x ] \
        || UPDATE_GRUB_DIVERT_DPKG_PATH=/usr/sbin/update-grub.anduinos-grub
    [ "${ANDUINOS_MIGRATION_UPDATE_GRUB_WRAPPER+x}" = x ] \
        || UPDATE_GRUB_LINK_TARGET=/usr/libexec/anduinos-update-grub
fi

remove_guard() {
    original=$1
    diverted=$2
    wrapper=$3
    dpkg_original=$4
    dpkg_diverted=$5
    owner=$($DPKG_DIVERT --listpackage "$dpkg_original" 2>/dev/null || true)
    [ "$owner" = anduinos-core-system ] || return 0
    if [ -e "$original" ] || [ -L "$original" ]; then
        if [ ! -L "$original" ] \
            || [ "$(readlink "$original")" != "$wrapper" ]; then
            printf '%s\n' "anduinos-core-system: refusing to remove an unknown wrapper: $original" >&2
            exit 1
        fi
        rm -f -- "$original"
    fi
    "$DPKG_DIVERT" --package anduinos-core-system --remove --rename \
        --divert "$dpkg_diverted" "$dpkg_original"
}

case "${1:-}" in
    remove|deconfigure)
        remove_guard "$UPDATE_GRUB" "$UPDATE_GRUB_DIVERT" \
            "$UPDATE_GRUB_LINK_TARGET" "$UPDATE_GRUB_DPKG_PATH" \
            "$UPDATE_GRUB_DIVERT_DPKG_PATH"
        remove_guard "$UPDATE_INITRAMFS" "$UPDATE_INITRAMFS_DIVERT" \
            "$UPDATE_INITRAMFS_LINK_TARGET" "$UPDATE_INITRAMFS_DPKG_PATH" \
            "$UPDATE_INITRAMFS_DIVERT_DPKG_PATH"
        ;;
esac
