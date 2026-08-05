set -eu

if { [ "$1" = "remove" ] || [ "$1" = "purge" ]; } && command -v update-initramfs >/dev/null 2>&1; then
    update-initramfs -u -k all
fi

#DEBHELPER#
exit 0
