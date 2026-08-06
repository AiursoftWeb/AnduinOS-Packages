set -eu

systemd-tmpfiles --create /usr/lib/tmpfiles.d/anduinos-btrfs-snapshots-manager.conf || true

for config_name in apt-snapshots.toml automation.toml; do
    target="/etc/anduinos-btrfs-snapshots-manager/$config_name"
    if [ -L "$target" ]; then
        echo "Refusing unsafe configuration symlink: $target" >&2
        exit 1
    fi
    if [ ! -e "$target" ]; then
        install -m644 -- \
            "/usr/share/anduinos-btrfs-snapshots-manager/defaults/$config_name" \
            "$target"
    fi
done

# GRUB cannot safely rewrite an environment block stored on Btrfs. Keep only
# Disk Snapshots Manager's one-shot selector on the EFI System Partition, where GRUB can
# clear it before entering the selected recovery entry.
if mountpoint -q /boot/efi; then
    install -d -m700 /boot/efi/EFI/anduinos
    if [ ! -e /boot/efi/EFI/anduinos/btrfs-snapshots-manager-grubenv ]; then
        /usr/bin/grub-editenv /boot/efi/EFI/anduinos/btrfs-snapshots-manager-grubenv create
    fi
fi

if [ -x /usr/sbin/update-grub ]; then
    # Disk Snapshots Manager must not inspect or mount unrelated disks while installing.
    # Its generated entries are independent of os-prober results.
    PATH="/usr/libexec/anduinos-btrfs-snapshots-manager/no-os-prober:/usr/sbin:/usr/bin:/sbin:/bin" \
        /usr/sbin/update-grub || \
        echo "Warning: Disk Snapshots Manager could not refresh the GRUB configuration" >&2
fi

systemctl daemon-reload || true
if [ -d /run/systemd/system ]; then
    systemctl enable --now anduinos-btrfs-snapshots-manager-scheduler.timer || true
    systemctl enable anduinos-btrfs-snapshots-manager-confirm.service || true
    systemctl start anduinos-btrfs-snapshots-manager-confirm.service || true
fi
dbus-send --system --type=method_call \
    --dest=org.freedesktop.DBus /org/freedesktop/DBus \
    org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
