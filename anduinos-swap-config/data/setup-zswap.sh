#!/bin/bash
# AnduinOS Zswap setup — reads declarative config, applies to sysfs.
# Config file (optional): /etc/default/anduinos-zswap
#   ZSWAP_ENABLED=yes|no
#   ZSWAP_COMPRESSOR=lz4
#   ZSWAP_MAX_POOL_PERCENT=20
#   ZSWAP_ACCEPT_THRESHOLD=90
#   ZSWAP_SHRINKER=Y|N
#
# If no config file exists, keep zswap disabled by default.
set -e

CONF="/etc/default/anduinos-zswap"
PARAM_DIR="/sys/module/zswap/parameters"
ENABLED="0"
COMPRESSOR="lz4"
POOL="20"
THRESHOLD="90"
SHRINKER="Y"

# Some kernels ship without zswap support. Treat that as "feature unavailable"
# rather than a hard boot-time failure.
[ -d "$PARAM_DIR" ] || exit 0

if [ -f "$CONF" ]; then
    set -a; . "$CONF"; set +a
    case "${ZSWAP_ENABLED:-no}" in
        yes|1|Y|true) ENABLED="1" ;;
        *) ENABLED="0" ;;
    esac
    COMPRESSOR="${ZSWAP_COMPRESSOR:-lz4}"
    POOL="${ZSWAP_MAX_POOL_PERCENT:-20}"
    THRESHOLD="${ZSWAP_ACCEPT_THRESHOLD:-90}"
    SHRINKER="${ZSWAP_SHRINKER:-Y}"
fi

echo "$ENABLED" > "$PARAM_DIR/enabled"
echo "$COMPRESSOR" > "$PARAM_DIR/compressor"
echo "$POOL" > "$PARAM_DIR/max_pool_percent"
echo "$THRESHOLD" > "$PARAM_DIR/accept_threshold_percent"
echo "$SHRINKER" > "$PARAM_DIR/shrinker_enabled"
