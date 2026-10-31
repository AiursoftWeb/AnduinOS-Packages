#!/bin/sh

# /run is carried across Dracut's switch_root.  This ephemeral proof therefore
# exists only when this boot actually passed through an AnduinOS Dracut image;
# an initramfs-tools fallback cannot inherit it from the previous boot.
proof_dir=/run/anduinos-dracut-migration
mkdir -p "$proof_dir" || return 1
{
    printf '%s\n' 'generator=dracut'
    printf 'kernel=%s\n' "$(uname -r)"
} > "$proof_dir/boot-proof" || return 1
chmod 0600 "$proof_dir/boot-proof" || return 1
