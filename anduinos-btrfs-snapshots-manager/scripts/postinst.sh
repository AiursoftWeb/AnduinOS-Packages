set -eu

if [ -x /usr/libexec/anduinos-dracut-verify ] && [ -d /lib/modules ]; then
    /usr/libexec/anduinos-dracut-verify --rebuild
elif command -v dracut >/dev/null 2>&1 && [ -d /lib/modules ]; then
    # Standalone installations outside anduinos-core-system retain a strict
    # fallback; unlike the old lifecycle, generation failures are never hidden.
    dracut --force --regenerate-all
fi

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

if [ -x /usr/libexec/anduinos-dracut-verify ]; then
    # Disk Snapshots Manager must not inspect or mount unrelated disks while installing.
    # Its generated entries are independent of os-prober results.
    PATH="/usr/libexec/anduinos-btrfs-snapshots-manager/no-os-prober:/usr/sbin:/usr/bin:/sbin:/bin" \
        /usr/libexec/anduinos-dracut-verify --update-grub
fi

# A recovery boot may have installed a one-boot unit under /run to shadow an
# older root's packaged service. Once this package is installed, that exact
# generated unit is stale and would continue to outrank the vendor unit even
# after daemon-reload. Remove only our two transient paths; preserve any admin
# overrides under /etc/systemd/system.
rm -f -- \
    /run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service \
    /run/systemd/system/multi-user.target.wants/anduinos-btrfs-snapshots-manager-confirm.service
systemctl daemon-reload || true
if [ -d /run/systemd/system ]; then
    systemctl enable --now anduinos-btrfs-snapshots-manager-scheduler.timer || true
    systemctl enable anduinos-btrfs-snapshots-manager-confirm.service || true
    systemctl start anduinos-btrfs-snapshots-manager-confirm.service || true
fi
dbus-send --system --type=method_call \
    --dest=org.freedesktop.DBus /org/freedesktop/DBus \
    org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
