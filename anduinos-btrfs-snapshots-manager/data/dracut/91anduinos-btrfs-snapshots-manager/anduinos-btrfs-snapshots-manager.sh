#!/bin/sh

command -v getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh
command -v det_fs >/dev/null 2>&1 || . /lib/fs-lib.sh

requested="$(getarg anduinos.btrfs_snapshots_manager 2>/dev/null || true)"
requested_protocol="$(getarg anduinos.btrfs_snapshots_manager_protocol 2>/dev/null || true)"

fail_or_skip() {
    if [ -n "$requested" ]; then
        die "Disk Snapshots Manager recovery did not start: $1"
        return 1
    fi
    warn "Disk Snapshots Manager skipped recovery: $1"
    return 0
}

root_spec="${root:-$(getarg root= 2>/dev/null || true)}"
case "$root_spec" in
    live:*) return 0 ;;
    block:*) root_spec="${root_spec#block:}" ;;
esac
case "$root_spec" in
    LABEL=*|UUID=*|PARTLABEL=*|PARTUUID=*)
        root_spec="$(label_uuid_to_dev "$root_spec")"
        ;;
esac
root_device="$(readlink -f "$root_spec" 2>/dev/null || true)"
if [ -z "$root_device" ] || [ ! -b "$root_device" ]; then
    fail_or_skip "the root device could not be resolved"
    return $?
fi

root_fstype="$(det_fs "$root_device" 2>/dev/null || true)"
if [ "$root_fstype" != btrfs ]; then
    fail_or_skip "the root filesystem is not Btrfs"
    return $?
fi

protocol_file=/etc/anduinos-btrfs-snapshots-manager/recovery-protocol-version
installed_protocol="$(cat "$protocol_file" 2>/dev/null || true)"
binary_protocol="$(/usr/libexec/anduinos-btrfs-snapshots-manager-initramfs --protocol-version 2>/dev/null || true)"
if [ "$installed_protocol" != 2 ] || [ "$binary_protocol" != "$installed_protocol" ]; then
    fail_or_skip "the Dracut recovery protocol is incomplete or inconsistent"
    return $?
fi
if [ -n "$requested" ] && [ "$requested_protocol" != "$installed_protocol" ]; then
    fail_or_skip "the requested recovery protocol is incompatible"
    return $?
fi

top_level=/run/anduinos-btrfs-snapshots-manager/top
mkdir -p "$top_level"
if ! mount -t btrfs -o rw,subvolid=5 "$root_device" "$top_level"; then
    fail_or_skip "the Btrfs top level could not be mounted"
    return $?
fi

transaction="$top_level/@snapshots/anduinos-btrfs-snapshots-manager/transactions/pending-rollback.json"
if [ ! -f "$transaction" ]; then
    umount "$top_level"
    return 0
fi

confirmation_engine=/usr/libexec/anduinos-btrfs-snapshots-manager-confirm
if ! chmod 0700 "$confirmation_engine"; then
    if [ -n "$requested" ]; then
        die "Disk Snapshots Manager could not activate its trusted confirmation engine"
        umount "$top_level"
        return 1
    fi
    warn "Disk Snapshots Manager could not activate its trusted confirmation engine"
fi

staged_confirmation=0
if /usr/libexec/anduinos-btrfs-snapshots-manager-initramfs --stage-confirmation-artifact; then
    staged_confirmation=1
elif [ -n "$requested" ]; then
    die "Disk Snapshots Manager could not stage its trusted confirmation engine"
    umount "$top_level"
    return 1
fi

if [ -n "$requested" ]; then
    /usr/libexec/anduinos-btrfs-snapshots-manager-initramfs "$requested" || status=$?
else
    /usr/libexec/anduinos-btrfs-snapshots-manager-initramfs || status=$?
fi
status="${status:-0}"
if [ "$status" -ne 0 ]; then
    warn "Disk Snapshots Manager could not complete the recovery transaction"
    if [ ! -d "$top_level/@root" ]; then
        die "Disk Snapshots Manager cannot find a bootable @root subvolume"
    fi
fi

if [ "$staged_confirmation" -eq 1 ] && [ -f "$transaction" ]; then
    reconciler_exec=/.snapshots/anduinos-btrfs-snapshots-manager/recovery-boot/confirm
    reconciler_unit=/run/systemd/system/anduinos-btrfs-snapshots-manager-confirm.service
    reconciler_wants=/run/systemd/system/multi-user.target.wants
    mkdir -p "$reconciler_wants" || die "Disk Snapshots Manager could not prepare userspace reconciliation"
    cat > "$reconciler_unit" <<EOF
[Unit]
Description=Reconcile a Disk Snapshots Manager recovery using the trusted initramfs engine
After=local-fs.target
RequiresMountsFor=/.snapshots /boot
ConditionPathExists=|/.snapshots/anduinos-btrfs-snapshots-manager/transactions/pending-rollback.json
ConditionDirectoryNotEmpty=|/.snapshots/anduinos-btrfs-snapshots-manager/cleanup-pending

[Service]
Type=oneshot
ExecStart=$reconciler_exec
User=root
Group=root
NoNewPrivileges=yes
PrivateTmp=yes
PrivateMounts=yes
ProtectSystem=strict
ProtectHome=read-only
ProtectHostname=yes
ProtectKernelLogs=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=CAP_SYS_ADMIN
SystemCallFilter=~@clock @cpu-emulation @debug @module @obsolete @raw-io @reboot @swap
RuntimeDirectory=anduinos-btrfs-snapshots-manager
ReadWritePaths=-/.snapshots -/boot/grub -/boot/efi -/run/anduinos-btrfs-snapshots-manager
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
    chmod 0600 "$reconciler_unit" || die "Disk Snapshots Manager could not protect its userspace reconciliation unit"
    ln -sf ../anduinos-btrfs-snapshots-manager-confirm.service \
        "$reconciler_wants/anduinos-btrfs-snapshots-manager-confirm.service" || \
        die "Disk Snapshots Manager could not activate userspace reconciliation"
fi

umount "$top_level" || die "Disk Snapshots Manager could not release the Btrfs top-level mount"
return 0
