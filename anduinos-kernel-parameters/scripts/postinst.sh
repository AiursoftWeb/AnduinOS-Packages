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

mark_reboot_required() {
    reboot_required_dir="${DPKG_ROOT:-}/run"
    package_name="anduinos-kernel-parameters"
    packages_file="$reboot_required_dir/reboot-required.pkgs"

    mkdir -p "$reboot_required_dir"
    touch "$reboot_required_dir/reboot-required"
    if ! grep -Fqx "$package_name" "$packages_file" 2>/dev/null; then
        printf '%s\n' "$package_name" >> "$packages_file"
    fi
}

if [ "$1" = "configure" ]; then
    rm -f "${DPKG_ROOT:-}/etc/default/grub.d/50-anduinos-desktop.cfg"
    if is_chroot; then
        echo "anduinos-kernel-parameters: chroot detected; deferring GRUB configuration refresh."
    elif command -v update-grub >/dev/null 2>&1; then
        update-grub
        mark_reboot_required
    fi
fi

#DEBHELPER#
exit 0
