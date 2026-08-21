#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Build-time dependency guards ──
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd git

ALSA_COMMIT="d2306b01aa595ae9d393f6852ffdbe2b226f4872"   # pinned for supply-chain safety

rm -rf "$SCRIPT_DIR/deploy" /tmp/alsa-ucm-conf
mkdir -p "$SCRIPT_DIR/deploy"
git clone https://github.com/alsa-project/alsa-ucm-conf.git /tmp/alsa-ucm-conf
git -C /tmp/alsa-ucm-conf checkout "$ALSA_COMMIT"

cp -a /tmp/alsa-ucm-conf/ucm2 "$SCRIPT_DIR/deploy/ucm2"
rm -rf /tmp/alsa-ucm-conf
echo "Done."
