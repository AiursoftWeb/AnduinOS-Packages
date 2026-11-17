#!/bin/sh

# GRUB adds this file to the initial cpio. It must start outside /run:
# Dracut mounts /run as tmpfs before executing this pre-udev hook.
# Only the existing systemd-cryptsetup handler opens the crypttab device.
anduinos_prepare_key() {
    incoming=/anduinos-unlock.key
    destination=/run/cryptsetup-keys.d/anduinos-root.key

    [ -e "$incoming" ] || [ -L "$incoming" ] || return 0
    if [ -L "$incoming" ] || [ ! -f "$incoming" ] \
        || [ "$(stat -c %s "$incoming")" != 64 ]; then
        warn "AnduinOS: invalid RAM unlock credential; use the disk password"
        rm -f -- "$incoming"
        return 0
    fi

    # Do not replace a key that another initramfs component has already supplied.
    if [ -e "$destination" ] || [ -L "$destination" ]; then
        warn "AnduinOS: an unlock credential already exists; keeping it"
        rm -f -- "$incoming"
        return 0
    fi

    mkdir -p -m 0700 /run/cryptsetup-keys.d || return 1
    chmod 0600 "$incoming" || return 1
    # Hooks run in separate processes. This RAM-only ownership marker lets
    # cleanup distinguish our file from another component's existing key.
    (umask 077; : > /run/anduinos-crypt-key-owned) || return 1
    if ! mv -- "$incoming" "$destination"; then
        rm -f -- /run/anduinos-crypt-key-owned
        return 1
    fi
}

anduinos_prepare_key
