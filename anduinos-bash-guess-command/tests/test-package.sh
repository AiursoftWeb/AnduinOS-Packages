#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
export ANDUINOS_GHOST_MODULE="$TEST_ROOT/anduinos-ghost.so"
# Native tests compile for the current host, not the package release matrix.
export CARGO_TARGET_DIR="$(realpath -m "${CARGO_TARGET_DIR:-$ROOT/engine/target}")"

bash -n "$ROOT/download.sh" "$ROOT/build-engine.sh" \
    "$ROOT/build-native.sh" "$ROOT/update-command-specs.sh" \
    "$ROOT/assets/anduinos-bash-guess-command" \
    "$ROOT/tests/test-interactive.sh" "$ROOT/tests/test-engine-runtime.sh" \
    "$ROOT/tests/test-readline-lifecycle.sh"

# The opt-out path must work even when package files do not exist.
bash --noprofile --norc -ic \
    'set -u; ANDUINOS_GUESS_COMMAND=0; source "$1"' bash \
    "$ROOT/assets/anduinos-bash-guess-command" 2>/dev/null

# Loading the package must preserve an existing programmable completion.
TERM=xterm script -qefc \
    "bash --noprofile --norc -ic 'complete -W sentinel apt; before=\$(complete -p apt); ANDUINOS_GUESS_ENGINE=0 source \"$ROOT/assets/anduinos-bash-guess-command\"; after=\$(complete -p apt); [[ \$before == \$after ]]'" \
    /dev/null >/dev/null

gcc -std=c11 -O2 -fPIC -Wall -Wextra -Werror -I"$ROOT/native" \
    -shared -o "$ANDUINOS_GHOST_MODULE" "$ROOT/native/anduinos_ghost.c"
bash "$ROOT/tests/test-readline-lifecycle.sh"

cargo test --locked --manifest-path "$ROOT/engine/Cargo.toml"
cargo build --locked --manifest-path "$ROOT/engine/Cargo.toml" --bin anduinos-quietd
export ANDUINOS_QUIETD="$CARGO_TARGET_DIR/debug/anduinos-quietd"
bash "$ROOT/tests/test-engine-runtime.sh"
bash "$ROOT/tests/test-interactive.sh"

printf 'All package integration checks passed.\n'
