#!/bin/sh
set -e
# Reload sysctl so that 30-anduinos-zram.conf (swappiness=100, page-cluster=0)
# takes effect immediately on install/upgrade.
if [ "$1" = "configure" ]; then
    sysctl --system >/dev/null 2>&1 || true
fi
