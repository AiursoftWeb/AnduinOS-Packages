#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="${1:-}"
INITRAMFS="${2:-}"
PROTOCOL="$(tr -d '\n' < "$PROJECT_ROOT/data/recovery-protocol-version")"

test "$PROTOCOL" = "2"
DRACUT_MODULE="$PROJECT_ROOT/data/dracut/91anduinos-btrfs-snapshots-manager/module-setup.sh"
DRACUT_HOOK="$PROJECT_ROOT/data/dracut/91anduinos-btrfs-snapshots-manager/anduinos-btrfs-snapshots-manager.sh"

grep -Fq 'det_fs "$root_device"' "$DRACUT_HOOK"
if rg -q '\$\{?FSTYPE' "$DRACUT_HOOK"; then
    echo "The Dracut pre-mount hook must not depend on an implicit FSTYPE variable" >&2
    exit 1
fi
grep -Fq 'recovery-protocol-version' "$DRACUT_MODULE"
grep -Fq 'anduinos-btrfs-snapshots-manager-confirm' "$DRACUT_MODULE"
grep -Fq 'recovery-protocol-version' "$PROJECT_ROOT/anduinos-btrfs-snapshots-manager.aosproj"
grep -Fq 'recovery-boot/confirm' "$DRACUT_HOOK"
if grep -Eq '^ExecStart=/run/' "$DRACUT_HOOK"; then
    echo "The recovery confirmation engine must not execute from a potentially noexec /run mount" >&2
    exit 1
fi
for unit_source in \
    "$PROJECT_ROOT/data/anduinos-btrfs-snapshots-manager-confirm.service" \
    "$DRACUT_HOOK"; do
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
    command -v lsinitrd >/dev/null
    listing="$(lsinitrd "$INITRAMFS" | awk '
        $1 ~ /^l/ && $(NF - 1) == "->" { print $(NF - 2); next }
        $1 ~ /^[bcdps-]/ { print $NF }
    ')"
    for member in \
        var/lib/dracut/hooks/pre-mount/50-anduinos-btrfs-snapshots-manager.sh \
        usr/libexec/anduinos-btrfs-snapshots-manager-initramfs \
        usr/libexec/anduinos-btrfs-snapshots-manager-confirm \
        etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version \
        usr/bin/cat usr/bin/chmod usr/bin/cp usr/bin/ln usr/bin/mkdir; do
        grep -Fxq "$member" <<< "$listing"
    done
fi

echo "recovery artifact checks passed"
