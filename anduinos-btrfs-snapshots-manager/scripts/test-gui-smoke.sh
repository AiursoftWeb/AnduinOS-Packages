#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BINARY="$ROOT/src/target/debug/anduinos-btrfs-snapshots-manager"

if ! command -v gtk4-broadwayd >/dev/null 2>&1; then
    echo "SKIP: gtk4-broadwayd is unavailable" >&2
    exit 77
fi

cargo build --manifest-path "$ROOT/src/Cargo.toml" --package anduinos-btrfs-snapshots-manager --offline

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

for display_number in 91 92 93 94 95 96 97 98 99; do
    broadway_log="$runtime_dir/broadway.log"
    gtk4-broadwayd ":$display_number" >"$broadway_log" 2>&1 &
    broadway_pid="$!"
    for _ in 1 2 3 4 5; do
        if kill -0 "$broadway_pid" 2>/dev/null; then
            break
        fi
        sleep 0.05
    done
    if kill -0 "$broadway_pid" 2>/dev/null; then
        break
    fi
    wait "$broadway_pid" 2>/dev/null || true
    broadway_pid=""
done

if [[ -z "$broadway_pid" ]]; then
    echo "SKIP: no Broadway display was available" >&2
    exit 77
fi

GDK_BACKEND=broadway \
BROADWAY_DISPLAY=":$display_number" \
G_DEBUG=fatal-criticals \
ANDUINOS_BTRFS_SNAPSHOTS_MANAGER_UI_SMOKE_TEST=1 \
timeout 15s "$BINARY"

echo "GTK construction/destruction smoke test passed"
