#!/bin/sh
set -eu
case "$1" in
  configure|triggered)
    # Retain Ubuntu's revocation compatibility check. Never downgrade to the
    # older shim to hide a revoked kernel: report it and preserve the old ESP.
    for kernel in /boot/vmlinuz-*; do
        [ -f "$kernel" ] || continue
        /usr/lib/shim/is-not-revoked "$kernel"
    done
    case "$(dpkg --print-architecture)" in
        amd64) suffix=x64 ;;
        arm64) suffix=aa64 ;;
        *) echo 'Unsupported shim architecture' >&2; exit 1 ;;
    esac
    update-alternatives --install "/usr/lib/shim/shim$suffix.efi.signed" \
        "shim$suffix.efi.signed" "/usr/lib/shim/shim$suffix.efi.signed.latest" 100
    dpkg-trigger --no-await anduinos-boot-chain-update
    ;;
esac
