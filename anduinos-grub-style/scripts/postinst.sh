set -eu

is_chroot() {
    if command -v systemd-detect-virt >/dev/null 2>&1 &&
       systemd-detect-virt --quiet --chroot >/dev/null 2>&1; then
        return 0
    fi

    if command -v ischroot >/dev/null 2>&1 && ischroot >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

if [ "$1" = "configure" ]; then
    if is_chroot; then
        echo "anduinos-grub-style: chroot detected; deferring GRUB configuration refresh."
    elif command -v update-grub >/dev/null 2>&1; then
        update-grub
    fi
fi

#DEBHELPER#
exit 0
