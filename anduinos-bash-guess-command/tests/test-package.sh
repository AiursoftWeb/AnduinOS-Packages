#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case $ARCH in
    amd64|x86_64) ARCH=amd64 ;;
    arm64|aarch64) ARCH=arm64 ;;
    *) printf 'SKIP: unsupported test architecture: %s\n' "$ARCH"; exit 0 ;;
esac
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

bash -n "$ROOT/download.sh" "$ROOT/build-engine.sh" \
    "$ROOT/build-native.sh" "$ROOT/update-command-specs.sh" \
    "$ROOT/assets/anduinos-bash-guess-command" \
    "$ROOT/assets/carapace-wrapper" "$ROOT/tests/test-interactive.sh" \
    "$ROOT/tests/test-engine-runtime.sh" "$ROOT/tests/test-offline.sh" \
    "$ROOT/tests/test-performance.sh"

grep -q 'anduinos-ghost.so' "$ROOT/anduinos-bash-guess-command.aosproj" ||
    fail 'native frontend is not packaged'
if grep -Eqi 'blesh|ble\.sh' "$ROOT/anduinos-bash-guess-command.aosproj" \
    "$ROOT/assets/anduinos-bash-guess-command" "$ROOT/download.sh"; then
    fail 'BLE remains in the package execution or build chain'
fi
grep -q 'enable -f.*anduinos-ghost.so' "$ROOT/assets/anduinos-bash-guess-command" ||
    fail 'loader does not enable the native frontend'
grep -q 'PROMPT_COMMAND' "$ROOT/assets/anduinos-bash-guess-command" ||
    fail 'command observations are not installed'
grep -q 'unshare --user --map-root-user --net' "$ROOT/assets/carapace-wrapper" ||
    fail 'Carapace is not isolated from the network'

# The opt-out path must work even when package files do not exist.
bash --noprofile --norc -ic \
    'set -u; ANDUINOS_GUESS_COMMAND=0; source "$1"' bash \
    "$ROOT/assets/anduinos-bash-guess-command" 2>/dev/null

cargo test --offline --manifest-path "$ROOT/engine/Cargo.toml"
bash "$ROOT/build-engine.sh" "$ARCH"
bash "$ROOT/build-native.sh" "$ARCH"
ANDUINOS_QUIETD="$ROOT/deploy/$ARCH/anduinos-quietd" \
    bash "$ROOT/tests/test-engine-runtime.sh"
bash "$ROOT/tests/test-interactive.sh"
bash "$ROOT/tests/test-offline.sh"
bash "$ROOT/tests/test-performance.sh"

printf 'All package integration checks passed.\n'
