#!/bin/bash
# Optional CPU qualification against public fixtures. No package construction,
# downloads, microphone access, or cross-package source staging.
set -euo pipefail
cd "$(dirname "$0")/.."
voice_root="$PWD"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$voice_root/tests/reference:$voice_root/src"
for tool in cargo python3; do
    command -v "$tool" >/dev/null || { echo "Missing test dependency: $tool" >&2; exit 1; }
done
export ANDUINOS_WHISPER_CLI="${ANDUINOS_WHISPER_CLI:-/usr/bin/whisper-cli}"
test -x "$ANDUINOS_WHISPER_CLI" || { echo "Missing CLI reference: $ANDUINOS_WHISPER_CLI" >&2; exit 1; }
for variable in ANDUINOS_VOICE_WORKER ANDUINOS_VOICE_MODEL ANDUINOS_VAD_MODEL; do
    value="${!variable:-}"
    test -f "$value" || { echo "$variable must name a source-built artifact or model fixture" >&2; exit 1; }
done
test -x "$ANDUINOS_VOICE_WORKER"
voice_results="$voice_root/obj/voice-test-results"
mkdir -p "$voice_results"
python3 "$voice_root/tests/benchmarks/benchmark-corpus.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --model "$ANDUINOS_VOICE_MODEL" > "$voice_results"/cpu-corpus.json
# The CLI comparison above establishes the historical accuracy baseline. Rust
# must match that backend's actual outputs and independently pass lifecycle,
# capture and resource checks. Never count Python-only tests as Rust coverage.
export ANDUINOS_VOICE_STRESS_REQUESTS=100
export ANDUINOS_VAD_STRESS_FRAMES=30000
cargo test --locked --test native_runtime -- --ignored --nocapture \
    --test-threads=1 --skip native_quick_selection \
    > "$voice_results"/rust-cpu-native.log 2>&1
cargo test --locked --lib native_capture -- --ignored --nocapture --test-threads=1 \
    > "$voice_results"/rust-capture-noise.log 2>&1
echo "CPU acceptance passed; reports: $voice_results"
