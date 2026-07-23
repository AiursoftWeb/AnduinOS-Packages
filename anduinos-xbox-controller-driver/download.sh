#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd git

# Fallback to github if the internal gitlab mirror is unreachable during local testing
REPO_URL="https://gitlab.aiursoft.com/mirror/xpadneo.git"
FALLBACK_URL="https://github.com/atar-axis/xpadneo.git"
COMMIT_ID="3acca9f"
VERSION="v0.11-pre-63-g3acca9f"

rm -rf "$SCRIPT_DIR/deploy" /tmp/xpadneo
echo "Cloning xpadneo..."
git clone "$REPO_URL" /tmp/xpadneo || git clone "$FALLBACK_URL" /tmp/xpadneo
git -C /tmp/xpadneo checkout "$COMMIT_ID"

echo "Structuring deploy folder..."
mkdir -p "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}"
mkdir -p "$SCRIPT_DIR/deploy/etc"

# Copy source files needed for DKMS
cp -a /tmp/xpadneo/hid-xpadneo/src "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}/"
cp /tmp/xpadneo/hid-xpadneo/Makefile "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}/"
# xpadneo generates dkms.conf from dkms.conf.in during its normal install, but we can just use the provided dkms.conf.in and replace VERSION.
sed -e "s/@DO_NOT_CHANGE@/$VERSION/g" /tmp/xpadneo/hid-xpadneo/dkms.conf.in > "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}/dkms.conf"
cp /tmp/xpadneo/hid-xpadneo/dkms.post_install "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}/"
cp /tmp/xpadneo/hid-xpadneo/dkms.post_remove "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}/"
chmod +x "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}/dkms.post_install"
chmod +x "$SCRIPT_DIR/deploy/usr/src/hid-xpadneo-${VERSION}/dkms.post_remove"

# Copy system configurations (udev and modprobe)
mkdir -p "$SCRIPT_DIR/deploy/etc/udev"
cp -r /tmp/xpadneo/hid-xpadneo/etc-udev-rules.d "$SCRIPT_DIR/deploy/etc/udev/rules.d"
cp -r /tmp/xpadneo/hid-xpadneo/etc-modprobe.d "$SCRIPT_DIR/deploy/etc/modprobe.d"

rm -rf /tmp/xpadneo
echo "Done. Prepared for DKMS packaging."
