#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="${1:?Usage: check-engine.sh ENGINE}"
PROTOCOL="$(tr -d '\n' < "$PROJECT_ROOT/data/recovery-protocol-version")"

test -x "$ENGINE"
test "$("$ENGINE" --protocol-version)" = "$PROTOCOL"

echo "Recovery engine protocol compatibility check passed"
