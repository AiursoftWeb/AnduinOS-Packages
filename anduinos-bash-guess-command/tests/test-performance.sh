#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
case $ARCH in
    amd64|x86_64) ARCH=amd64 ;;
    arm64|aarch64) ARCH=arm64 ;;
    *) printf 'SKIP: unsupported performance-test architecture: %s\n' "$ARCH"; exit 0 ;;
esac
REAL_CARAPACE="$ROOT/deploy/$ARCH/carapace"
if [[ ! -x $REAL_CARAPACE ]]; then
    printf 'SKIP: run bash download.sh %s before performance tests.\n' "$ARCH"
    exit 0
fi
if ! unshare --user --map-root-user --net true 2>/dev/null; then
    printf 'SKIP: runtime does not permit the production network sandbox.\n'
    exit 0
fi

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/package/bin"
ln -s "$REAL_CARAPACE" "$TEST_ROOT/package/carapace"
sed "s|/usr/lib/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    "$ROOT/assets/carapace-wrapper" >"$TEST_ROOT/package/bin/carapace"
chmod 755 "$TEST_ROOT/package/bin/carapace"
WRAPPER="$TEST_ROOT/package/bin/carapace"

elapsed_ms() {
    local start end
    start="$(date +%s%N)"
    "$@" >/dev/null
    end="$(date +%s%N)"
    REPLY=$(((end-start)/1000000))
}

elapsed_ms "$WRAPPER" _carapace bash
init_ms=$REPLY
((init_ms <= 350)) || fail "Carapace initialization took ${init_ms}ms (limit 350ms)"

# Warm filesystem and Carapace caches before measuring the interactive path.
ANDUINOS_GUESS_STATIC_TIMEOUT=200ms "$WRAPPER" git bash git statu >/dev/null
times=()
for _ in {1..9}; do
    elapsed_ms env ANDUINOS_GUESS_STATIC_TIMEOUT=200ms \
        "$WRAPPER" git bash git statu
    times+=("$REPLY")
done
mapfile -t sorted < <(printf '%s\n' "${times[@]}" | sort -n)
p50_ms=${sorted[4]}
p95_ms=${sorted[8]}
((p95_ms <= 300)) || fail "static completion p95 was ${p95_ms}ms (limit 300ms)"

installed_script_bytes=$(wc -c \
    <"$ROOT/assets/anduinos-bash-guess-command")
((installed_script_bytes += $(wc -c <"$ROOT/assets/carapace-wrapper")))
((installed_script_bytes += $(wc -c <"$ROOT/assets/anduinos-guess-context.bash")))
((installed_script_bytes <= 49152)) ||
    fail "installed integration scripts exceed 48 KiB (${installed_script_bytes} bytes)"

printf 'Performance checks passed: init=%sms, static p50=%sms, p95=%sms, scripts=%s bytes.\n' \
    "$init_ms" "$p50_ms" "$p95_ms" "$installed_script_bytes"
