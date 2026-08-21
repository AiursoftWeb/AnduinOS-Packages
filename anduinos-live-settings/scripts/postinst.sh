set -eu

if [ "$1" = "configure" ] && command -v update-initramfs >/dev/null 2>&1; then
    update-initramfs -u -k all
fi

#DEBHELPER#
exit 0
