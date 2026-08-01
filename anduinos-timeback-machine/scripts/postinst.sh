#!/bin/sh
set -e

if command -v systemctl >/dev/null 2>&1; then
    # `systemctl enable` also works in an offline chroot. Do not require a
    # running systemd instance: the installer invokes package scripts in the
    # target before its first boot.
    systemctl enable anduinos-timeback-confirm.service >/dev/null 2>&1 || true
    systemctl enable anduinos-timeback-maintenance.timer >/dev/null 2>&1 || true
    systemctl daemon-reload || true
fi

if command -v dbus-send >/dev/null 2>&1; then
    dbus-send --system --type=method_call \
        --dest=org.freedesktop.DBus \
        /org/freedesktop/DBus \
        org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
fi
