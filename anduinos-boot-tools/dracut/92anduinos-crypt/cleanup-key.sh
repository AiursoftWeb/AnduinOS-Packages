#!/bin/sh

# systemd automatically discovers this key when crypttab's key field is "none".
# Such auto-discovered keys are not erased by systemd's keyfile-erase option.
# Delete our transient copies once the root mapping exists, and check again
# before switch_root because /run is moved into the installed system.
if [ ! -e /run/anduinos-crypt-key-owned ]; then
    rm -f -- /anduinos-unlock.key
    return 0
fi

if [ -e /run/cryptsetup-keys.d/anduinos-root.key ] \
    && [ ! -b /dev/mapper/anduinos-root ]; then
    return 1
fi
rm -f -- /anduinos-unlock.key /run/cryptsetup-keys.d/anduinos-root.key \
    /run/anduinos-crypt-key-owned
