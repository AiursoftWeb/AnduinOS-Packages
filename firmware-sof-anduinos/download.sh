#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SOF_VERSION="2025.12"
SOF_URL="https://github.com/thesofproject/sof-bin/releases/download/v${SOF_VERSION}/sof-bin-${SOF_VERSION}.tar.gz"

rm -rf "$SCRIPT_DIR/deploy" /tmp/sof-dl
mkdir -p "$SCRIPT_DIR/deploy" /tmp/sof-dl

echo "Downloading SOF firmware v${SOF_VERSION}..."
wget -q "$SOF_URL" -O /tmp/sof-dl/sof-firmware.tar.gz

mv /tmp/sof-dl/sof-firmware.tar.gz "$SCRIPT_DIR/deploy/sof-firmware.tar.gz"
rm -rf /tmp/sof-dl
echo "Done."
