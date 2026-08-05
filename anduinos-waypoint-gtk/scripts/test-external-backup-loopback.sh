#!/usr/bin/env bash
set -euo pipefail

# Non-destructive integration test for the kernel/userspace Btrfs assumptions
# used by the external-backup importer. It creates one sparse loopback image
# under /tmp and never addresses a host block device.

for command in mkfs.btrfs btrfs mount umount findmnt truncate; do
    command -v "$command" >/dev/null || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
sudo -n true 2>/dev/null || {
    echo "Passwordless sudo is required for the disposable loopback test" >&2
    exit 77
}

test_root=$(mktemp -d /tmp/anduinos-waypoint-btrfs.XXXXXX)
image="$test_root/filesystem.img"
mount_point="$test_root/mount"
stream="$test_root/root.btrfs"

cleanup() {
    if findmnt -rn --target "$mount_point" >/dev/null 2>&1; then
        sudo -n umount "$mount_point"
    fi
    case "$test_root" in
        /tmp/anduinos-waypoint-btrfs.*)
            sudo -n find "$test_root" -depth -delete
            ;;
        *)
            echo "Refusing to clean unexpected test path: $test_root" >&2
            return 1
            ;;
    esac
}
trap cleanup EXIT

truncate -s 512M "$image"
truncate -s 0 "$stream"
chmod 0600 "$stream"
mkfs.btrfs -q -f "$image"
mkdir "$mount_point"
sudo -n mount -o loop "$image" "$mount_point"

sudo -n btrfs subvolume create "$mount_point/source" >/dev/null
sudo -n install -m 0644 /etc/os-release "$mount_point/source/os-release"
sudo -n btrfs subvolume snapshot -r "$mount_point/source" "$mount_point/root" >/dev/null
sudo -n btrfs send --proto 1 -f "$stream" "$mount_point/root"
sudo -n btrfs receive --dump <"$stream" >/dev/null

for staging in staging-first staging-second; do
    sudo -n mkdir "$mount_point/$staging"
    sudo -n btrfs receive --chroot --max-errors 1 "$mount_point/$staging" <"$stream" >/dev/null
    test "$(sudo -n btrfs property get -ts "$mount_point/$staging/root" ro)" = "ro=true"
    sudo -n cmp "$mount_point/source/os-release" "$mount_point/$staging/root/os-release"
    sudo -n btrfs subvolume show "$mount_point/$staging/root" | grep -q 'Received UUID:'
done

first_uuid=$(sudo -n btrfs subvolume show "$mount_point/staging-first/root" | awk '/^[[:space:]]*UUID:/ {print $2; exit}')
second_uuid=$(sudo -n btrfs subvolume show "$mount_point/staging-second/root" | awk '/^[[:space:]]*UUID:/ {print $2; exit}')
first_received=$(sudo -n btrfs subvolume show "$mount_point/staging-first/root" | awk '/Received UUID:/ {print $3; exit}')
second_received=$(sudo -n btrfs subvolume show "$mount_point/staging-second/root" | awk '/Received UUID:/ {print $3; exit}')

test -n "$first_uuid"
test -n "$second_uuid"
test "$first_uuid" != "$second_uuid"
test -n "$first_received"
test "$first_received" = "$second_received"

echo "External backup loopback test passed"
echo "First local UUID:  $first_uuid"
echo "Second local UUID: $second_uuid"
echo "Received UUID:     $first_received"
