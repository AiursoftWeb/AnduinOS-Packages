#!/bin/sh
set -e

if command -v systemctl >/dev/null 2>&1; then
    if [ "${1:-}" = "remove" ] || [ "${1:-}" = "purge" ]; then
        systemctl disable anduinos-timeback-confirm.service >/dev/null 2>&1 || true
        systemctl disable anduinos-timeback-maintenance.timer >/dev/null 2>&1 || true
    fi
    systemctl daemon-reload || true
fi

if command -v dbus-send >/dev/null 2>&1; then
    dbus-send --system --type=method_call \
        --dest=org.freedesktop.DBus \
        /org/freedesktop/DBus \
        org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
fi
