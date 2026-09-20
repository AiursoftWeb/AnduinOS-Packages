#!/bin/bash
# Optional CPU qualification against public fixtures. No package construction,
# downloads, microphone access, or cross-package source staging.
set -euo pipefail
cd "$(dirname "$0")/.."
voice_root="$PWD"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$voice_root/src"
for tool in python3 whisper-cli; do
    command -v "$tool" >/dev/null || { echo "Missing test dependency: $tool" >&2; exit 1; }
done
for variable in ANDUINOS_VOICE_WORKER ANDUINOS_VOICE_MODEL ANDUINOS_VAD_MODEL; do
    value="${!variable:-}"
    test -f "$value" || { echo "$variable must name a source-built artifact or model fixture" >&2; exit 1; }
done
test -x "$ANDUINOS_VOICE_WORKER"
voice_results="$voice_root/obj/voice-test-results"
mkdir -p "$voice_results"
python3 "$voice_root/tests/benchmarks/benchmark-corpus.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --model "$ANDUINOS_VOICE_MODEL" > "$voice_results"/cpu-corpus.json
python3 "$voice_root/tests/benchmarks/stress-resident.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --model "$ANDUINOS_VOICE_MODEL" \
    --backend cpu --requests 100 > "$voice_results"/cpu-stress.json 2> "$voice_results"/cpu-stress-progress.log
python3 "$voice_root/tests/benchmarks/benchmark-capture.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --model "$ANDUINOS_VOICE_MODEL" \
    --vad-model "$ANDUINOS_VAD_MODEL" --normalize-dbfs -26 > "$voice_results"/capture-corpus-ci.json
python3 "$voice_root/tests/benchmarks/benchmark-noise.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --vad-model "$ANDUINOS_VAD_MODEL" > "$voice_results"/noise-shapes-ci.json
python3 "$voice_root/tests/benchmarks/stress-vad.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --vad-model "$ANDUINOS_VAD_MODEL" \
    > "$voice_results"/vad-stress-ci.json 2> "$voice_results"/vad-stress-ci-progress.log
echo "CPU acceptance passed; reports: $voice_results"
