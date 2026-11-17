#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Build-time dependency guards ──
source "$SCRIPT_DIR/../lib/build-guards.sh"
need_cmd git

ALSA_COMMIT="d260eedd1900dbf3b477047e048d5382a5f50e24"   # pinned for supply-chain safety

rm -rf "$SCRIPT_DIR/deploy" /tmp/alsa-ucm-conf
mkdir -p "$SCRIPT_DIR/deploy"
git clone https://github.com/alsa-project/alsa-ucm-conf.git /tmp/alsa-ucm-conf
git -C /tmp/alsa-ucm-conf checkout "$ALSA_COMMIT"

cp -a /tmp/alsa-ucm-conf/ucm2 "$SCRIPT_DIR/deploy/ucm2"
# Preserve upstream terms alongside the configuration files.
cp /tmp/alsa-ucm-conf/LICENSE "$SCRIPT_DIR/deploy/copyright"
cat >> "$SCRIPT_DIR/deploy/copyright" <<'EOF'

The files marked SPDX-License-Identifier: MIT retain their individual
copyright notices in the configuration files. Their MIT terms follow:

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
EOF
rm -rf /tmp/alsa-ucm-conf
echo "Done."
