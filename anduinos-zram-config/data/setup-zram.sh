#!/bin/bash
# Default zram setup: 50% of total RAM, lz4 compression, swap priority 100
set -e
MEM=$(awk '/MemTotal/{printf "%.0f",$2/2048}' /proc/meminfo)
DEV=$(zramctl -f -s "${MEM}M" -a lz4)
mkswap "$DEV"
swapon -p 100 "$DEV"
