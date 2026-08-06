set -eu

if [ "${1:-}" = "purge" ]; then
    rm -f -- /etc/anduinos-waypoint/apt-snapshots.toml
    rm -f -- /etc/anduinos-waypoint/automation.toml
    rmdir -- /etc/anduinos-waypoint 2>/dev/null || true
    rmdir -- /var/lib/anduinos-waypoint 2>/dev/null || true

    if mountpoint -q /boot/efi; then
        rm -f -- /boot/efi/EFI/anduinos/waypoint-grubenv
        rmdir -- /boot/efi/EFI/anduinos 2>/dev/null || true
    fi
fi

if [ -x /usr/sbin/update-grub ]; then
    prober_stub_dir="$(mktemp -d /run/anduinos-waypoint-grub.XXXXXX)" || prober_stub_dir=""
    case "$prober_stub_dir" in
        /run/anduinos-waypoint-grub.*)
            ln -s /bin/true "$prober_stub_dir/os-prober"
            ln -s /bin/true "$prober_stub_dir/linux-boot-prober"
            PATH="$prober_stub_dir:/usr/sbin:/usr/bin:/sbin:/bin" \
                /usr/sbin/update-grub || \
                echo "Warning: Waypoint could not refresh the GRUB configuration" >&2
            rm -f -- "$prober_stub_dir/os-prober" "$prober_stub_dir/linux-boot-prober"
            rmdir -- "$prober_stub_dir" || true
            ;;
        *)
            echo "Warning: Waypoint could not create a private GRUB refresh directory" >&2
            ;;
    esac
fi
