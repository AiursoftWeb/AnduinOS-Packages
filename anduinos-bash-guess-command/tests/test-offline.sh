#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! unshare --user --map-root-user --net true 2>/dev/null; then
    printf 'SKIP: runtime does not permit unprivileged network namespaces.\n'
    exit 0
fi

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
mkdir -p "$TEST_ROOT/package/bin"

cat >"$TEST_ROOT/package/carapace" <<'EOF'
#!/usr/bin/env bash
readlink /proc/self/ns/net
EOF
chmod 755 "$TEST_ROOT/package/carapace"
sed "s|/usr/lib/anduinos-bash-guess-command|$TEST_ROOT/package|g" \
    "$ROOT/assets/carapace-wrapper" >"$TEST_ROOT/package/bin/carapace"
chmod 755 "$TEST_ROOT/package/bin/carapace"

parent_namespace="$(readlink /proc/self/ns/net)"
completion_namespace="$(
    ANDUINOS_GUESS_STATIC_TIMEOUT=200ms \
        "$TEST_ROOT/package/bin/carapace" probe
)"
[[ $completion_namespace == net:* ]] || fail 'sandbox probe returned no namespace'
[[ $completion_namespace != "$parent_namespace" ]] ||
    fail 'Carapace completion shares the caller network namespace'

init_namespace="$("$TEST_ROOT/package/bin/carapace" _carapace bash)"
[[ $init_namespace == net:* ]] || fail 'initialization sandbox returned no namespace'
[[ $init_namespace != "$parent_namespace" ]] ||
    fail 'Carapace initialization shares the caller network namespace'

mkdir -p "$TEST_ROOT/denied-bin"
cat >"$TEST_ROOT/denied-bin/unshare" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod 755 "$TEST_ROOT/denied-bin/unshare"
if PATH="$TEST_ROOT/denied-bin:$PATH" \
    "$TEST_ROOT/package/bin/carapace" probe >"$TEST_ROOT/denied.out" 2>/dev/null; then
    fail 'Carapace did not fail closed when network isolation was denied'
fi
[[ ! -s "$TEST_ROOT/denied.out" ]] ||
    fail 'Carapace emitted a completion after network isolation failed'

printf 'All offline isolation checks passed.\n'
