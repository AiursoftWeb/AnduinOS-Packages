set -eu

# This script is the synchronous safety boundary for both APT and PackageKit.
# A new package has not been unpacked when preinst runs, so keep it
# self-contained and use only Essential tools plus the already-installed GRUB.

BOOT_DIR=${ANDUINOS_MIGRATION_BOOT_DIR:-${DPKG_ROOT:-}/boot}
STATE_DIR=${ANDUINOS_MIGRATION_STATE_DIR:-${DPKG_ROOT:-}/var/lib/anduinos-dracut-migration}
FALLBACK_DIR=${ANDUINOS_MIGRATION_FALLBACK_DIR:-$BOOT_DIR/anduinos-dracut-migration}
GRUB_GENERATOR=${ANDUINOS_MIGRATION_GRUB_GENERATOR:-${DPKG_ROOT:-}/etc/grub.d/06_anduinos_dracut_migration_fallback}
GRUB_GENERATOR_LATE=${ANDUINOS_MIGRATION_GRUB_GENERATOR_LATE:-${DPKG_ROOT:-}/etc/grub.d/41_anduinos_dracut_migration_fallback}
GRUB_DEFAULT_DROPIN=${ANDUINOS_MIGRATION_GRUB_DEFAULT_DROPIN:-${DPKG_ROOT:-}/etc/default/grub.d/99-anduinos-dracut-migration.cfg}
GRUB_CFG=${ANDUINOS_MIGRATION_GRUB_CFG:-$BOOT_DIR/grub/grub.cfg}
PROC_CMDLINE=${ANDUINOS_MIGRATION_PROC_CMDLINE:-/proc/cmdline}
GRUB_MKCONFIG=${ANDUINOS_MIGRATION_GRUB_MKCONFIG:-grub-mkconfig}
UNAME=${ANDUINOS_MIGRATION_UNAME:-uname}
DF=${ANDUINOS_MIGRATION_DF:-df}
INITRD_INSPECTOR=${ANDUINOS_MIGRATION_INITRD_INSPECTOR:-}
FAIL_AT=${ANDUINOS_MIGRATION_FAIL_AT:-}

log() {
    printf '%s\n' "anduinos-dracut-migration: $*" >&2
}

checkpoint() {
    if [ "$FAIL_AT" = "$1" ]; then
        log "injected failure at $1"
        exit 75
    fi
}

sync_path() {
    sync -f "$1"
}

atomic_marker() {
    marker=$1
    temporary="$STATE_DIR/.$marker.new"
    : > "$temporary"
    chmod 0600 "$temporary"
    sync_path "$temporary"
    mv -f "$temporary" "$STATE_DIR/$marker"
    sync_path "$STATE_DIR"
}

safe_directory() {
    directory=$1
    if [ -L "$directory" ]; then
        log "refusing unsafe directory symlink: $directory"
        exit 1
    fi
    install -d -m0755 "$directory"
}

stage_file() {
    source_file=$1
    target_file=$2
    temporary="$target_file.new"

    if [ -s "$target_file" ] && cmp -s -- "$source_file" "$target_file"; then
        return 0
    fi
    rm -f -- "$temporary"
    # A hard link is not a backup: an in-place writer would corrupt both names.
    # Reflink gives Btrfs/XFS a cheap independent inode; cp transparently falls
    # back to a full copy on filesystems without copy-on-write support.
    cp --reflink=auto --preserve=mode,timestamps -- "$source_file" "$temporary"
    sync_path "$temporary"
    mv -f -- "$temporary" "$target_file"
    sync_path "$(dirname "$target_file")"
}

find_boot_pair() {
    running_version=$($UNAME -r 2>/dev/null || true)
    if [ -n "$running_version" ] \
        && [ -s "$BOOT_DIR/vmlinuz-$running_version" ] \
        && [ -s "$BOOT_DIR/initrd.img-$running_version" ]; then
        printf '%s\n' "$running_version"
        return 0
    fi

    selected=
    for kernel in "$BOOT_DIR"/vmlinuz-*; do
        [ -s "$kernel" ] || continue
        version=${kernel##*/vmlinuz-}
        [ -s "$BOOT_DIR/initrd.img-$version" ] || continue
        selected=$version
    done
    [ -n "$selected" ] || return 1
    printf '%s\n' "$selected"
}

write_grub_generator() {
    temporary="$GRUB_GENERATOR.new"
    safe_directory "$(dirname "$GRUB_GENERATOR")"
    cat > "$temporary" <<'EOF'
#!/bin/sh
set -e

prefix=/usr
exec_prefix=/usr
datarootdir=/usr/share
. "${pkgdatadir:-/usr/share/grub}/grub-mkconfig_lib"

fallback_dir=${ANDUINOS_MIGRATION_FALLBACK_DIR:-/boot/anduinos-dracut-migration}
fallback_kernel=$fallback_dir/fallback-vmlinuz
fallback_initrd=$fallback_dir/fallback-initrd.img
fallback_cmdline=$fallback_dir/cmdline

[ -s "$fallback_kernel" ] || exit 0
[ -s "$fallback_initrd" ] || exit 0
[ -s "$fallback_cmdline" ] || exit 0

grub_probe_command=${ANDUINOS_MIGRATION_GRUB_PROBE:-${grub_probe:-/usr/sbin/grub-probe}}
fallback_device=$($grub_probe_command --target=device "$fallback_kernel")
kernel_path=$(make_system_path_relative_to_its_root "$fallback_kernel")
initrd_path=$(make_system_path_relative_to_its_root "$fallback_initrd")
cmdline=$(cat "$fallback_cmdline")

cat <<GRUB_EOF
menuentry 'AnduinOS pre-Dracut migration fallback' --class anduinos --class gnu-linux --class os {
$(prepare_grub_to_access_device "$fallback_device" | sed 's/^/\t/')
	linux	$kernel_path $cmdline
	initrd	$initrd_path
}
GRUB_EOF
EOF
    chmod 0755 "$temporary"
    sync_path "$temporary"
    mv -f "$temporary" "$GRUB_GENERATOR"
    sync_path "$(dirname "$GRUB_GENERATOR")"
}

write_default_override() {
    temporary="$GRUB_DEFAULT_DROPIN.new"
    safe_directory "$(dirname "$GRUB_DEFAULT_DROPIN")"
    printf '%s\n' \
        '# Temporary safety policy for the initramfs-tools to Dracut migration.' \
        'GRUB_DEFAULT=0' \
        'GRUB_SAVEDEFAULT=false' > "$temporary"
    chmod 0644 "$temporary"
    sync_path "$temporary"
    mv -f "$temporary" "$GRUB_DEFAULT_DROPIN"
    sync_path "$(dirname "$GRUB_DEFAULT_DROPIN")"
}

atomic_update_grub() {
    [ ! -L "$GRUB_CFG" ] || {
        log "refusing to replace a symlinked GRUB configuration: $GRUB_CFG"
        return 1
    }
    staged="$GRUB_CFG.anduinos-new"
    rm -f -- "$staged" "$staged.new"
    "$GRUB_MKCONFIG" -o "$staged"
    [ -s "$staged" ] || {
        log "grub-mkconfig produced an empty configuration"
        return 1
    }
    sync_path "$staged"
    mv -f -- "$staged" "$GRUB_CFG"
    sync_path "$(dirname "$GRUB_CFG")"
}

manifest_value() {
    sed -n "s/^$1=//p" "$STATE_DIR/fallback.manifest" | head -n 1
}

verify_preserved_fallback() {
    [ -s "$STATE_DIR/fallback.manifest" ] || return 1
    [ -f "$FALLBACK_DIR/fallback-vmlinuz" ] \
        && [ ! -L "$FALLBACK_DIR/fallback-vmlinuz" ] || return 1
    [ -f "$FALLBACK_DIR/fallback-initrd.img" ] \
        && [ ! -L "$FALLBACK_DIR/fallback-initrd.img" ] || return 1
    [ -s "$FALLBACK_DIR/cmdline" ] && [ ! -L "$FALLBACK_DIR/cmdline" ] \
        || return 1

    expected_kernel_size=$(manifest_value kernel_size)
    expected_initrd_size=$(manifest_value initrd_size)
    expected_kernel_sha256=$(manifest_value kernel_sha256)
    expected_initrd_sha256=$(manifest_value initrd_sha256)
    [ -n "$expected_kernel_size" ] && [ -n "$expected_initrd_size" ] \
        && [ -n "$expected_kernel_sha256" ] \
        && [ -n "$expected_initrd_sha256" ] || return 1
    [ "$(stat -c %s "$FALLBACK_DIR/fallback-vmlinuz")" \
        = "$expected_kernel_size" ] || return 1
    [ "$(stat -c %s "$FALLBACK_DIR/fallback-initrd.img")" \
        = "$expected_initrd_size" ] || return 1
    [ "$(sha256sum "$FALLBACK_DIR/fallback-vmlinuz" | awk '{print $1}')" \
        = "$expected_kernel_sha256" ] || return 1
    [ "$(sha256sum "$FALLBACK_DIR/fallback-initrd.img" | awk '{print $1}')" \
        = "$expected_initrd_sha256" ] || return 1
}

reuse_preserved_fallback() {
    verify_preserved_fallback || {
        log "the sealed migration fallback is incomplete or corrupted; refusing to overwrite it"
        exit 1
    }

    if [ -s "$GRUB_CFG" ] \
        && grep -Fq 'anduinos-dracut-migration/fallback-vmlinuz' "$GRUB_CFG" \
        && grep -Fq 'anduinos-dracut-migration/fallback-initrd.img' "$GRUB_CFG"; then
        log "reusing the sealed pre-Dracut fallback"
        exit 0
    fi

    # A later package may have regenerated GRUB after an interrupted attempt.
    # Restore the already-sealed fallback without ever copying over it from the
    # now potentially changed active initrd.
    rm -f -- "$GRUB_GENERATOR_LATE"
    write_grub_generator
    write_default_override
    atomic_update_grub
    awk '/^[[:space:]]*menuentry / { print; exit }' "$GRUB_CFG" \
        | grep -Fq 'AnduinOS pre-Dracut migration fallback'
    log "restored GRUB access to the sealed pre-Dracut fallback"
    exit 0
}

inspect_legacy_initrd() {
    image=$1
    inspector=$INITRD_INSPECTOR
    if [ -z "$inspector" ]; then
        if command -v lsinitramfs >/dev/null 2>&1; then
            inspector=lsinitramfs
        elif command -v lsinitrd >/dev/null 2>&1; then
            inspector=lsinitrd
        else
            log "no installed tool can inspect the legacy initrd"
            return 1
        fi
    fi
    "$inspector" "$image" >/dev/null
}

check_staging_space() {
    largest_initrd=0
    for candidate in "$BOOT_DIR"/initrd.img-*; do
        [ -s "$candidate" ] || continue
        candidate_size=$(stat -c %s "$candidate")
        if [ "$candidate_size" -gt "$largest_initrd" ]; then
            largest_initrd=$candidate_size
        fi
    done
    [ "$largest_initrd" -gt 0 ] || return 1

    # Dracut must be able to build beside the active image.  Keep an extra
    # 64 MiB for growth, manifests, and a staged GRUB configuration.
    required_kb=$(((largest_initrd + 67108864 + 1023) / 1024))
    available_kb=$($DF -Pk "$BOOT_DIR" | awk 'NR == 2 { print $4 }')
    case "$available_kb" in
        *[!0-9]*|'')
            log "unable to determine free space on $BOOT_DIR"
            return 1
            ;;
    esac
    if [ "$available_kb" -lt "$required_kb" ]; then
        log "insufficient free space on $BOOT_DIR: need ${required_kb} KiB, have ${available_kb} KiB"
        return 1
    fi
}

case "${1:-}" in
    install|upgrade) ;;
    *) exit 0 ;;
esac

# A fresh installation is already created with Dracut by the installer. Future
# core upgrades also skip once this migration has durably completed.
old_version=${2:-}
[ -n "$old_version" ] || exit 0
[ ! -e "$STATE_DIR/complete" ] || exit 0

safe_directory "$STATE_DIR"
safe_directory "$FALLBACK_DIR"

if [ -e "$STATE_DIR/fallback-ready" ]; then
    reuse_preserved_fallback
fi

version=$(find_boot_pair) || {
    log "no non-empty kernel and initrd pair is available; refusing the transition"
    exit 1
}
case "$version" in
    *[!A-Za-z0-9._+-]*|'')
        log "unsafe kernel version: $version"
        exit 1
        ;;
esac

kernel="$BOOT_DIR/vmlinuz-$version"
initrd="$BOOT_DIR/initrd.img-$version"

inspect_legacy_initrd "$initrd" || {
    log "the selected legacy initrd is not readable: $initrd"
    exit 1
}

checkpoint before_fallback_kernel
stage_file "$kernel" "$FALLBACK_DIR/fallback-vmlinuz"
checkpoint after_fallback_kernel
stage_file "$initrd" "$FALLBACK_DIR/fallback-initrd.img"
checkpoint after_fallback_initrd
checkpoint before_space_check
check_staging_space
checkpoint after_space_check

# Do not carry PackageKit's special update boot back into the fallback boot.
# Other arguments describe the known-working root/storage configuration.
awk '
    {
        for (field = 1; field <= NF; field++) {
            if ($field ~ /^(BOOT_IMAGE|initrd|systemd\.unit|rd\.systemd\.unit)=/) {
                continue
            }
            printf "%s%s", separator, $field
            separator = " "
        }
        print ""
    }
' "$PROC_CMDLINE" > "$FALLBACK_DIR/cmdline.new"
[ -s "$FALLBACK_DIR/cmdline.new" ] || printf '%s\n' ro > "$FALLBACK_DIR/cmdline.new"
chmod 0600 "$FALLBACK_DIR/cmdline.new"
sync_path "$FALLBACK_DIR/cmdline.new"
mv -f "$FALLBACK_DIR/cmdline.new" "$FALLBACK_DIR/cmdline"
sync_path "$FALLBACK_DIR"

checkpoint before_manifest
manifest_new="$STATE_DIR/fallback.manifest.new"
{
    printf 'kernel_version=%s\n' "$version"
    printf 'kernel_size=%s\n' "$(stat -c %s "$FALLBACK_DIR/fallback-vmlinuz")"
    printf 'initrd_size=%s\n' "$(stat -c %s "$FALLBACK_DIR/fallback-initrd.img")"
    printf 'kernel_sha256=%s\n' "$(sha256sum "$FALLBACK_DIR/fallback-vmlinuz" | awk '{print $1}')"
    printf 'initrd_sha256=%s\n' "$(sha256sum "$FALLBACK_DIR/fallback-initrd.img" | awk '{print $1}')"
} > "$manifest_new"
chmod 0600 "$manifest_new"
sync_path "$manifest_new"
mv -f "$manifest_new" "$STATE_DIR/fallback.manifest"
sync_path "$STATE_DIR"
checkpoint after_manifest

cmp -s -- "$kernel" "$FALLBACK_DIR/fallback-vmlinuz"
cmp -s -- "$initrd" "$FALLBACK_DIR/fallback-initrd.img"

write_grub_generator
write_default_override
checkpoint before_update_grub
atomic_update_grub
checkpoint after_update_grub

grep -Fq 'anduinos-dracut-migration/fallback-vmlinuz' "$GRUB_CFG"
grep -Fq 'anduinos-dracut-migration/fallback-initrd.img' "$GRUB_CFG"
awk '/^[[:space:]]*menuentry / { print; exit }' "$GRUB_CFG" \
    | grep -Fq 'AnduinOS pre-Dracut migration fallback'

atomic_marker fallback-ready
checkpoint after_fallback_ready
log "durable legacy fallback is ready for kernel $version"
