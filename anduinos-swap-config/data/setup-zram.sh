#!/bin/bash
# AnduinOS Zram setup — reads optional user config, falls back to 50% RAM.
# Config file (optional): /etc/default/anduinos-zram
#   ZRAM_ENABLED=yes|no          — enable or disable zram entirely
#   ZRAM_DEVICE_COUNT=N          — number of devices (multi-device format)
#   ZRAM_0_SIZE_MB=15899         — size in MiB for device 0
#   ZRAM_0_ALGORITHM=lz4         — compression algorithm for device 0
#   ZRAM_0_PRIORITY=100          — swap priority for device 0
#   (same pattern for device 1, 2, …)
#
# If no config file exists or ZRAM_ENABLED is not "no", falls back to:
#   50% of total RAM, lz4, swap priority 100.
set -e

# Teardown: remove all existing zram devices so we can rebuild from scratch.
# This makes systemctl restart anduinos-zram.service work correctly.
for dev in /dev/zram*; do
    [ -b "$dev" ] || continue
    swapoff "$dev" 2>/dev/null || true
    zramctl -r "$dev" 2>/dev/null || true
done

CONF="/etc/default/anduinos-zram"
ALGO="lz4"
PRIORITY=100

# ── Read user config if present ──────────────────────────────────────────────
if [ -f "$CONF" ]; then
    # source the config file to get shell variables
    set -a; . "$CONF"; set +a

    if [ "${ZRAM_ENABLED:-yes}" = "no" ]; then
        echo "Zram disabled by user config ($CONF)."
        exit 0
    fi

    # Multi-device format
    if [ -n "${ZRAM_DEVICE_COUNT:-}" ] && [ "$ZRAM_DEVICE_COUNT" -gt 0 ]; then
        i=0
        while [ "$i" -lt "$ZRAM_DEVICE_COUNT" ]; do
            eval "SIZE=\${ZRAM_${i}_SIZE_MB:-}"
            eval "DEV_ALGO=\${ZRAM_${i}_ALGORITHM:-lz4}"
            eval "DEV_PRI=\${ZRAM_${i}_PRIORITY:-100}"

            if [ -z "$SIZE" ]; then
                i=$((i + 1))
                continue
            fi

            DEV=$(zramctl -f -s "${SIZE}M" -a "$DEV_ALGO")
            mkswap "$DEV"
            swapon -p "$DEV_PRI" "$DEV"
            echo "Created $DEV: ${SIZE} MiB, $DEV_ALGO, priority $DEV_PRI"

            i=$((i + 1))
        done
        exit 0
    fi

    # Single-device format (backward compatible)
    if [ -n "${ZRAM_SIZE_MB:-}" ]; then
        DEV=$(zramctl -f -s "${ZRAM_SIZE_MB}M" -a "${ZRAM_ALGORITHM:-lz4}")
        mkswap "$DEV"
        swapon -p "${ZRAM_PRIORITY:-100}" "$DEV"
        exit 0
    fi
fi

# ── Fallback: 50% of total RAM ────────────────────────────────────────────────
MEM=$(awk '/MemTotal/{printf "%.0f",$2/2048}' /proc/meminfo)
DEV=$(zramctl -f -s "${MEM}M" -a "$ALGO")
mkswap "$DEV"
swapon -p "$PRIORITY" "$DEV"
echo "Created $DEV: ${MEM} MiB (50% RAM), $ALGO, priority $PRIORITY"
