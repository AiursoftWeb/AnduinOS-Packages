#!/bin/sh
set -e

# Reconcile generated PAM mappings after an upgrade and migrate the readable
# policy snapshot used by the unprivileged GTK frontend. Never make package
# configuration fail because an administrator has intentionally customized or
# damaged old state; the GUI can report and repair that state later.
if [ "${1:-}" = "configure" ] && [ -x /usr/lib/anduinos-yubikey-manager/helper ]; then
    if ! /usr/lib/anduinos-yubikey-manager/helper repair; then
        echo "anduinos-yubikey-manager: could not reconcile existing authentication state" >&2
    fi
fi
