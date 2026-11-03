#!/bin/bash

# The installer reserves this crypttab name for its single encrypted root.
# Other encrypted layouts keep using upstream Dracut without this handoff.
anduinos_root_entry() {
    local name device key options extra
    local entry= count=0
    local crypttab="${dracutsysrootdir-}/etc/crypttab"

    [[ -r $crypttab ]] || return 1
    while read -r name device key options extra; do
        [[ $name == anduinos-root ]] || continue
        ((count += 1))
        [[ $device =~ ^UUID=[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]] || return 1
        [[ $key == none && -z $extra ]] || return 1
        [[ ,$options, == *,luks,* && ,$options, == *,x-initrd.attach,* ]] || return 1
        entry="$name $device $key $options"
    done < "$crypttab"

    [[ $count == 1 ]] || return 1
    printf '%s\n' "$entry"
}

check() {
    anduinos_root_entry > /dev/null || return 1
    require_binaries cryptsetup || return 1
    return 0
}

depends() {
    # Upstream owns device discovery, password prompts, root mounting and
    # switch_root. This module only bridges the transient GRUB credential.
    # Explicit-module builds also need the services that execute Dracut hooks.
    # systemd-cryptsetup alone can boot, but would skip our pre-udev handoff.
    echo "crypt systemd-cryptsetup dracut-systemd"
}

install() {
    local entry
    entry=$(anduinos_root_entry) || return 1

    # Generic images do not normally copy crypttab. Host-only builds may have
    # already filtered it; retain those entries and add only our root if absent.
    mkdir -p "$initdir/etc"
    if ! grep -q '^anduinos-root[[:space:]]' "$initdir/etc/crypttab" 2>/dev/null; then
        printf '%s\n' "$entry" >> "$initdir/etc/crypttab"
    fi

    # Never install /etc/anduinos-luks/machine.key or the incoming RAM file.
    # GRUB supplies the latter dynamically after opening the encrypted root.
    inst_multiple mkdir chmod mv rm stat
    inst_hook pre-udev 10 "$moddir/prepare-key.sh"
    inst_hook initqueue/finished 99 "$moddir/cleanup-key.sh"
    inst_hook pre-pivot 01 "$moddir/cleanup-key.sh"
}
