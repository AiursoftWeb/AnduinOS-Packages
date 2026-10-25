#!/bin/sh
set -eu
if [ -f /usr/libexec/anduinos-dkms-original ]; then
    if [ -e /usr/sbin/dkms ] && [ "$(readlink /usr/sbin/dkms 2>/dev/null || true)" != /usr/libexec/anduinos-dkms-wrapper ]; then
        echo 'Unexpected DKMS frontend; refusing to replace it' >&2
        exit 1
    fi
    ln -sf /usr/libexec/anduinos-dkms-wrapper /usr/sbin/dkms
fi
if [ "${1:-}" = triggered ]; then
    case " ${2:-} " in
      *' anduinos-boot-chain-update '*) /usr/libexec/anduinos-grub-update package-deploy ;;
    esac
    exit 0
fi
if [ "${1:-}" = configure ]; then
    # APKG only auto-enables units on a fresh package install. This unit is
    # new in -3, so enable it for upgrades too, without undoing later user
    # decisions to disable/mask it.
    if [ -d /run/systemd/system ] && dpkg --compare-versions "${2:-0}" lt 2.0.5-3; then
        systemctl daemon-reload
        systemctl enable anduinos-signing-defaults.service || true
    fi
    # Also converge on upgrades where the certificate was already enrolled.
    # No managed identity or incomplete enrollment is a harmless no-op.
    if ! /usr/libexec/anduinos-secureboot-helper sync-defaults; then
        echo 'AnduinOS signing defaults were not synchronized. Open Driver Center to repair them; existing driver signatures were not changed.' >&2
    fi
fi
