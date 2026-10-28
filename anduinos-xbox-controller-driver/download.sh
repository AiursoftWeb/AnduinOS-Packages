#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd git

REPO_URL="https://github.com/atar-axis/xpadneo.git"
COMMIT_ID="547eb90cd9f01de64db888b558f7e1a606c71965"
VERSION="v0.11-pre-86-g547eb90"

rm -rf "$SCRIPT_DIR/deploy" /tmp/xpadneo
echo "Cloning xpadneo..."
git clone "$REPO_URL" /tmp/xpadneo
git -C /tmp/xpadneo checkout --detach "$COMMIT_ID"

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

# Preserve the licensing guide and full texts from the same upstream checkout.
DOC_DIR="$SCRIPT_DIR/deploy/usr/share/doc/anduinos-xbox-controller-driver"
mkdir -p "$DOC_DIR/LICENSES"
install -m 0644 /tmp/xpadneo/LICENSE.md "$DOC_DIR/LICENSE.md"
install -m 0644 /tmp/xpadneo/LICENSES/LICENSE.gpl-2.0.txt "$DOC_DIR/LICENSES/"
install -m 0644 /tmp/xpadneo/LICENSES/LICENSE.gpl-3.0.txt "$DOC_DIR/LICENSES/"

rm -rf /tmp/xpadneo
echo "Done. Prepared for DKMS packaging."
