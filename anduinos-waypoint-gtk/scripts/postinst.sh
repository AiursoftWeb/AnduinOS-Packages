set -eu

systemd-tmpfiles --create /usr/lib/tmpfiles.d/anduinos-waypoint.conf || true

if [ ! -e /etc/anduinos-waypoint/schedules.toml ]; then
    install -m644 /usr/share/anduinos-waypoint/defaults/schedules.toml \
        /etc/anduinos-waypoint/schedules.toml
fi

# GRUB cannot safely rewrite an environment block stored on Btrfs. Keep only
# Waypoint's one-shot selector on the EFI System Partition, where GRUB can
# clear it before entering the selected recovery entry.
if mountpoint -q /boot/efi; then
    install -d -m700 /boot/efi/EFI/anduinos
    if [ ! -e /boot/efi/EFI/anduinos/waypoint-grubenv ]; then
        /usr/bin/grub-editenv /boot/efi/EFI/anduinos/waypoint-grubenv create
    fi
fi

if [ -x /usr/sbin/update-grub ]; then
    # Waypoint must not inspect or mount unrelated disks while installing.
    # Its generated entries are independent of os-prober results.
    PATH="/usr/libexec/anduinos-waypoint/no-os-prober:/usr/sbin:/usr/bin:/sbin:/bin" \
        /usr/sbin/update-grub || \
        echo "Warning: Waypoint could not refresh the GRUB configuration" >&2
fi

systemctl daemon-reload || true
if [ -d /run/systemd/system ]; then
    systemctl enable anduinos-waypoint-confirm.service || true
    systemctl start anduinos-waypoint-confirm.service || true
fi
dbus-send --system --type=method_call \
    --dest=org.freedesktop.DBus /org/freedesktop/DBus \
    org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
