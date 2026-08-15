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
    "$ROOT/tests/test-interactive.sh" "$ROOT/tests/test-engine-runtime.sh" \
    "$ROOT/tests/test-readline-lifecycle.sh"

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
if grep -Eqi 'carapace|(^|[[:space:]])complete([[:space:]]|$)' \
    "$ROOT/assets/anduinos-bash-guess-command" \
    "$ROOT/anduinos-bash-guess-command.aosproj"; then
    fail 'the runtime package still modifies Tab completion'
fi
if grep -Eq 'std::net|TcpStream|UdpSocket|AF_INET|AF_INET6|getaddrinfo|Command::new\("(curl|wget|nc)"\)' \
    "$ROOT"/engine/src/*.rs "$ROOT"/native/*.c \
    "$ROOT/assets/anduinos-bash-guess-command"; then
    fail 'installed runtime contains a network client primitive'
fi
if grep -Eq 'download\.sh|update-command-specs\.sh' \
    "$ROOT/anduinos-bash-guess-command.aosproj"; then
    fail 'ordinary package builds invoke a networked development workflow'
fi
if grep -Eq '<(Pre|Post)(Install|Remove|Uninstall)|systemd|cron' \
    "$ROOT/anduinos-bash-guess-command.aosproj"; then
    fail 'package metadata installs a lifecycle hook or system service'
fi

# The opt-out path must work even when package files do not exist.
bash --noprofile --norc -ic \
    'set -u; ANDUINOS_GUESS_COMMAND=0; source "$1"' bash \
    "$ROOT/assets/anduinos-bash-guess-command" 2>/dev/null

# Loading the package must preserve an existing programmable completion.
TERM=xterm script -qefc \
    "bash --noprofile --norc -ic 'complete -W sentinel apt; before=\$(complete -p apt); ANDUINOS_GUESS_ENGINE=0 source \"$ROOT/assets/anduinos-bash-guess-command\"; after=\$(complete -p apt); [[ \$before == \$after ]]'" \
    /dev/null >/dev/null

bash "$ROOT/build-native.sh" "$ARCH"
bash "$ROOT/tests/test-readline-lifecycle.sh"

cargo test --offline --manifest-path "$ROOT/engine/Cargo.toml"
grammar="$ROOT/engine/specs/generated-command-tree.tsv"
grammar_nodes=$(awk -F '\t' 'NR > 1 { count++ } END { print count + 0 }' "$grammar")
grammar_roots=$(awk -F '\t' 'NR > 1 && $1 !~ / / { count++ } END { print count + 0 }' "$grammar")
path_slots=$(awk -F '\t' '$4 == "path" { count++ } END { print count + 0 }' "$grammar")
((grammar_nodes >= 7000 && grammar_roots >= 700 && path_slots >= 1900)) ||
    fail "generated grammar corpus is incomplete: $grammar_roots roots, $grammar_nodes nodes, $path_slots path slots"
bash "$ROOT/build-engine.sh" "$ARCH"
runtime_bytes=$((
    $(stat -c %s "$ROOT/deploy/$ARCH/anduinos-quietd") +
    $(stat -c %s "$ROOT/deploy/$ARCH/anduinos-ghost.so")
))
((runtime_bytes < 20 * 1024 * 1024)) ||
    fail "runtime payload exceeds 20 MiB: $runtime_bytes bytes"
ANDUINOS_QUIETD="$ROOT/deploy/$ARCH/anduinos-quietd" \
    bash "$ROOT/tests/test-engine-runtime.sh"
bash "$ROOT/tests/test-interactive.sh"

printf 'All package integration checks passed.\n'
