#!/bin/sh
set -e

if command -v systemctl >/dev/null 2>&1; then
    systemctl stop anduinos-timebackd.service >/dev/null 2>&1 || true
    systemctl stop anduinos-timeback-confirm.service >/dev/null 2>&1 || true
    systemctl stop anduinos-timeback-maintenance.timer >/dev/null 2>&1 || true
    systemctl stop anduinos-timeback-maintenance.service >/dev/null 2>&1 || true
fi
