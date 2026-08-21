#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="${1:-}"
INITRAMFS="${2:-}"
PROTOCOL="$(tr -d '\n' < "$PROJECT_ROOT/data/recovery-protocol-version")"

test "$PROTOCOL" = "2"
grep -Fq 'get_fstype "$root_device"' "$PROJECT_ROOT/data/initramfs-local-premount"
if rg -q '\$\{?FSTYPE' "$PROJECT_ROOT/data/initramfs-local-premount"; then
    echo "The initramfs premount script must not depend on a non-exported FSTYPE variable" >&2
    exit 1
fi
grep -Fq 'recovery-protocol-version' "$PROJECT_ROOT/data/initramfs-hook"
grep -Fq 'anduinos-btrfs-snapshots-manager-confirm' "$PROJECT_ROOT/data/initramfs-hook"
grep -Fq 'recovery-protocol-version' "$PROJECT_ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'recovery-boot/confirm' "$PROJECT_ROOT/data/initramfs-local-premount"
if grep -Eq '^ExecStart=/run/' "$PROJECT_ROOT/data/initramfs-local-premount"; then
    echo "The recovery confirmation engine must not execute from a potentially noexec /run mount" >&2
    exit 1
fi
for unit_source in \
    "$PROJECT_ROOT/data/anduinos-btrfs-snapshots-manager-confirm.service" \
    "$PROJECT_ROOT/data/initramfs-local-premount"; do
    grep -Fq 'After=local-fs.target' "$unit_source"
    grep -Fq 'RequiresMountsFor=/.snapshots /boot' "$unit_source"
    if grep -Fq 'After=multi-user.target' "$unit_source"; then
        echo "The confirmation service must not create a multi-user.target ordering cycle" >&2
        exit 1
    fi
done

if [ -n "$ENGINE" ]; then
    test -x "$ENGINE"
    test "$($ENGINE --protocol-version)" = "$PROTOCOL"
fi

if [ -n "$INITRAMFS" ]; then
    test -f "$INITRAMFS"
    command -v lsinitramfs >/dev/null
    listing="$(lsinitramfs "$INITRAMFS")"
    for member in \
        scripts/local-premount/anduinos-btrfs-snapshots-manager \
        usr/libexec/anduinos-btrfs-snapshots-manager-initramfs \
        usr/libexec/anduinos-btrfs-snapshots-manager-confirm \
        etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version \
        usr/bin/cat usr/bin/chmod usr/bin/cp usr/bin/ln usr/bin/mkdir; do
        grep -Fxq "$member" <<< "$listing"
    done
fi

echo "recovery artifact checks passed"
