#!/bin/sh
set -e
# Reload sysctl so that 30-anduinos-swap.conf (swappiness=100, page-cluster=0)
# takes effect immediately on install/upgrade.
if [ "$1" = "configure" ]; then
    sysctl --system >/dev/null 2>&1 || true

    # Repair upgrades from builds that mistakenly enabled zswap by default.
    # If the user has not created an explicit zswap config, apply the new
    # default (disabled) immediately and keep the service opt-in.
    if [ ! -f /etc/default/anduinos-zswap ]; then
        systemctl restart anduinos-zswap.service >/dev/null 2>&1 || true
        systemctl disable anduinos-zswap.service >/dev/null 2>&1 || true
    fi
fi
