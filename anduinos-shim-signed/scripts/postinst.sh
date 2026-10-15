#!/bin/sh
set -eu
parameters=$(python3 -m anduinos_secureboot.architecture shell "$(dpkg --print-architecture)")
eval "$parameters"
case "$1" in
  configure|triggered)
    # Retain Ubuntu's revocation compatibility check. Never downgrade to the
    # older shim to hide a revoked kernel: report it and preserve the old ESP.
    for kernel in /boot/vmlinuz-*; do
        [ -f "$kernel" ] || continue
        /usr/lib/shim/is-not-revoked "$kernel"
    done
    update-alternatives --install /usr/lib/shim/shim${BOOT_EFI_SUFFIX}.efi.signed \
        shim${BOOT_EFI_SUFFIX}.efi.signed /usr/lib/shim/shim${BOOT_EFI_SUFFIX}.efi.signed.latest 100
    dpkg-trigger --no-await anduinos-boot-chain-update
    ;;
esac
