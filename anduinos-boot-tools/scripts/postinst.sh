set -eu

STATE_DIR=${ANDUINOS_MIGRATION_STATE_DIR:-${DPKG_ROOT:-}/var/lib/anduinos-dracut-migration}
BOOT_DIR=${ANDUINOS_MIGRATION_BOOT_DIR:-${DPKG_ROOT:-}/boot}
GRUB_GENERATOR_EARLY=${ANDUINOS_MIGRATION_GRUB_GENERATOR:-${DPKG_ROOT:-}/etc/grub.d/06_anduinos_dracut_migration_fallback}
GRUB_GENERATOR_LATE=${ANDUINOS_MIGRATION_GRUB_GENERATOR_LATE:-${DPKG_ROOT:-}/etc/grub.d/41_anduinos_dracut_migration_fallback}
GRUB_CFG=${ANDUINOS_MIGRATION_GRUB_CFG:-$BOOT_DIR/grub/grub.cfg}
VERIFY=${ANDUINOS_MIGRATION_VERIFY:-${DPKG_ROOT:-}/usr/libexec/anduinos-dracut-verify}
GRUB_MKCONFIG=${ANDUINOS_MIGRATION_GRUB_MKCONFIG:-grub-mkconfig}
SYSTEMCTL=${ANDUINOS_MIGRATION_SYSTEMCTL:-systemctl}
FAIL_AT=${ANDUINOS_MIGRATION_FAIL_AT:-}
BOOT_ID_FILE=${ANDUINOS_MIGRATION_BOOT_ID_FILE:-/proc/sys/kernel/random/boot_id}

log() {
    printf '%s\n' "anduinos-dracut-migration: $*" >&2
}

checkpoint() {
    if [ "$FAIL_AT" = "$1" ]; then
        log "injected failure at $1"
        exit 75
    fi
}

atomic_marker() {
    marker=$1
    temporary="$STATE_DIR/.$marker.new"
    : > "$temporary"
    chmod 0600 "$temporary"
    sync -f "$temporary"
    mv -f "$temporary" "$STATE_DIR/$marker"
    sync -f "$STATE_DIR"
}

atomic_update_grub() {
    [ ! -L "$GRUB_CFG" ] || {
        log "refusing to replace a symlinked GRUB configuration: $GRUB_CFG"
        return 1
    }
    staged="$GRUB_CFG.anduinos-new"
    rm -f -- "$staged" "$staged.new"
    "$GRUB_MKCONFIG" -o "$staged"
    [ -s "$staged" ]
    sync -f "$staged"
    mv -f -- "$staged" "$GRUB_CFG"
    sync -f "$(dirname "$GRUB_CFG")"
}

[ "${1:-}" = configure ] || exit 0
[ -e "$STATE_DIR/fallback-ready" ] || exit 0
[ ! -e "$STATE_DIR/complete" ] || exit 0

atomic_marker packages-switched
checkpoint before_rebuild
"$VERIFY" --rebuild
checkpoint after_rebuild
"$VERIFY" --verify
atomic_marker images-verified
checkpoint after_images_verified

# Preserve the fallback entry, but move it after normal Linux entries. Keep
# forcing GRUB_DEFAULT=0 until a later boot proves that the first normal entry
# really reached userspace through Dracut. The confirmation service then
# removes this temporary override and restores the user's GRUB policy.
if [ -e "$GRUB_GENERATOR_EARLY" ]; then
    mv -f "$GRUB_GENERATOR_EARLY" "$GRUB_GENERATOR_LATE"
fi
sync -f "$(dirname "$GRUB_GENERATOR_LATE")"
checkpoint before_final_update_grub
atomic_update_grub
checkpoint after_final_update_grub

"$VERIFY" --verify-default
grep -Fq 'anduinos-dracut-migration/fallback-vmlinuz' "$GRUB_CFG"
grep -Fq 'anduinos-dracut-migration/fallback-initrd.img' "$GRUB_CFG"
if awk '/^[[:space:]]*menuentry / { print; exit }' "$GRUB_CFG" \
    | grep -Fq 'AnduinOS pre-Dracut migration fallback'; then
    log "the migration fallback is still the default GRUB entry"
    exit 1
fi

atomic_marker transaction-complete

boot_id=$(cat "$BOOT_ID_FILE")
case "$boot_id" in
    *[!A-Fa-f0-9-]*|'')
        log "unable to record the completing boot ID"
        exit 1
        ;;
esac
boot_id_new="$STATE_DIR/.completed-boot-id.new"
printf '%s\n' "$boot_id" > "$boot_id_new"
chmod 0600 "$boot_id_new"
sync -f "$boot_id_new"
mv -f "$boot_id_new" "$STATE_DIR/completed-boot-id"
sync -f "$STATE_DIR"
atomic_marker complete

if [ -d "${DPKG_ROOT:-}/run/systemd/system" ]; then
    "$SYSTEMCTL" disable --now anduinos-dracut-migration.timer >/dev/null 2>&1 || true
fi
log "Dracut images are verified; normal GRUB boot is restored"
