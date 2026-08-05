#!/usr/bin/env bash
set -euo pipefail

# Exercise OperationEngine with the real Btrfs command and a disposable sparse
# image. The host root, host Btrfs filesystems, and real block devices are never
# accepted as test targets.

for command in mkfs.btrfs btrfs mount umount findmnt truncate cargo jq; do
    command -v "$command" >/dev/null || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
sudo -n true 2>/dev/null || {
    echo "Passwordless sudo is required for the disposable loopback test" >&2
    exit 77
}

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d /tmp/anduinos-waypoint-operations.XXXXXX)"
image="$test_root/filesystem.img"
mount_point="$test_root/mount"

cleanup() {
    if findmnt -rn --target "$mount_point" >/dev/null 2>&1; then
        sudo -n umount "$mount_point"
    fi
    case "$test_root" in
        /tmp/anduinos-waypoint-operations.*)
            sudo -n find "$test_root" -depth -delete
            ;;
        *)
            echo "Refusing to clean unexpected test path: $test_root" >&2
            return 1
            ;;
    esac
}
trap cleanup EXIT

truncate -s 1G "$image"
mkfs.btrfs -q -f "$image"
mkdir "$mount_point"
sudo -n mount -o loop "$image" "$mount_point"

for subvolume in @root @home @log @snapshots @containers @libvirt; do
    sudo -n btrfs subvolume create "$mount_point/$subvolume" >/dev/null
done

kernel_release="$(tr -d '\n' </proc/sys/kernel/osrelease)"
sudo -n install -D -m 0644 /proc/sys/kernel/osrelease \
    "$mount_point/@root/proc/sys/kernel/osrelease"
sudo -n install -D -m 0644 /etc/os-release \
    "$mount_point/@root/etc/os-release"
sudo -n install -D -m 0644 /etc/os-release \
    "$mount_point/@root/var/lib/dpkg/status"
sudo -n install -D -m 0644 /etc/os-release \
    "$mount_point/@root/boot/initrd.img-$kernel_release"
sudo -n install -D -m 0644 /etc/os-release \
    "$mount_point/@root/boot/vmlinuz-$kernel_release"

test_binary="$({
    cargo test \
        --manifest-path "$script_root/src/Cargo.toml" \
        -p anduinos-recovery-engine \
        --test loopback_operations \
        --no-run \
        --message-format=json
} | jq -r 'select(.profile.test == true and .executable != null) | .executable' | tail -n 1)"
test -n "$test_binary"
test -x "$test_binary"

sudo -n env \
    ANDUINOS_WAYPOINT_LOOPBACK_ROOT="$mount_point" \
    "$test_binary" \
    --ignored \
    real_btrfs_ \
    --nocapture \
    --test-threads=1

echo "Recovery operation loopback qualification passed"
