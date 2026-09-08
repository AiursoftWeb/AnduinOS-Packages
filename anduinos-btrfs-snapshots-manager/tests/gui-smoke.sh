#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="$(realpath -m "${CARGO_TARGET_DIR:-$ROOT/src/target}/debug/anduinos-btrfs-snapshots-manager")"

if ! command -v gtk4-broadwayd >/dev/null 2>&1; then
    echo "The gui profile requires gtk4-broadwayd." >&2
    exit 1
fi

cargo build --manifest-path "$ROOT/src/Cargo.toml" --package anduinos-btrfs-snapshots-manager --locked

runtime_dir="$(mktemp -d -t anduinos-btrfs-snapshots-manager-gui-smoke.XXXXXX)"
broadway_pid=""
cleanup() {
    if [[ -n "$broadway_pid" ]]; then
        kill "$broadway_pid" 2>/dev/null || true
        wait "$broadway_pid" 2>/dev/null || true
    fi
    rm -rf -- "$runtime_dir"
}
trap cleanup EXIT

# Both HTTP and GTK sockets live in our private runtime directory. There is no
# shared port/display search and no race between "process exists" and readiness.
export XDG_RUNTIME_DIR="$runtime_dir"
export XDG_CONFIG_HOME="$runtime_dir/config"
export XDG_CACHE_HOME="$runtime_dir/cache"
export GSETTINGS_BACKEND=memory GIO_USE_VFS=local GTK_A11Y=none
broadway_log="$runtime_dir/broadway.log"
gtk4-broadwayd --unixsocket="$runtime_dir/http.socket" :0 >"$broadway_log" 2>&1 &
broadway_pid=$!
deadline=$((SECONDS + 10))
until [[ -S "$runtime_dir/broadway1.socket" ]]; do
    if ! kill -0 "$broadway_pid" 2>/dev/null || ((SECONDS >= deadline)); then
        printf 'Broadway failed to become ready:\n' >&2
        tail -30 "$broadway_log" >&2
        exit 1
    fi
    sleep 0.02
done

for surface in advanced information; do
    GDK_BACKEND=broadway \
    BROADWAY_DISPLAY=":0" \
    G_DEBUG=fatal-criticals \
    ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_UI_SMOKE_TEST="$surface" \
    timeout 15s "$BINARY"
done

echo "GTK construction/destruction smoke test passed"
