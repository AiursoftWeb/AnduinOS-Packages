set -eu

case "${1:-}" in
    remove|purge)
        if command -v systemd-detect-virt >/dev/null 2>&1 \
                && systemd-detect-virt --quiet --chroot; then
            echo 'anduinos-hyperfluent-grub-theme: GRUB refresh deferred in chroot.'
        elif command -v ischroot >/dev/null 2>&1 && ischroot; then
            echo 'anduinos-hyperfluent-grub-theme: GRUB refresh deferred in chroot.'
        else
            update-grub
        fi
        ;;
esac

#DEBHELPER#
exit 0
