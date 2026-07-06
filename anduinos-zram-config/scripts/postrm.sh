#!/bin/sh
set -e
# When zram-config is removed, 30-anduinos-zram.conf (swappiness=100) is gone.
# Reload sysctl so the next-highest-priority file takes over:
#   20-anduinos-tweaks.conf (swappiness=10) from system-tweaks, or
#   kernel default (60) if system-tweaks is also not installed.
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    sysctl --system >/dev/null 2>&1 || true
fi
