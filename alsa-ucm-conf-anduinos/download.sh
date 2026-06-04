#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ALSA_COMMIT="9cbe982"   # pinned for supply-chain safety

rm -rf "$SCRIPT_DIR/deploy" /tmp/alsa-ucm-conf
mkdir -p "$SCRIPT_DIR/deploy"
git clone --depth 1 https://github.com/alsa-project/alsa-ucm-conf.git /tmp/alsa-ucm-conf
git -C /tmp/alsa-ucm-conf checkout "$ALSA_COMMIT" 2>/dev/null || true

cp -a /tmp/alsa-ucm-conf/ucm2 "$SCRIPT_DIR/deploy/ucm2"
rm -rf /tmp/alsa-ucm-conf
echo "Done."
