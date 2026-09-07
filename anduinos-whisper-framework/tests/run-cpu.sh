#!/bin/bash
# Same entry point for CI and local regression. Never installs host packages,
# opens a microphone, or claims GPU coverage. Output contains public-fixture
# test results and performance metadata only.
set -euo pipefail
cd "$(dirname "$0")/../.."
voice_root="$PWD"
export PYTHONDONTWRITEBYTECODE=1
for tool in python3 node dbus-run-session glib-compile-schemas whisper-cli xvfb-run msgfmt; do
    command -v "$tool" >/dev/null || { echo "Missing test dependency: $tool" >&2; exit 1; }
done
if [ -z "${ANDUINOS_VOICE_WORKER:-}" ]; then
    bash anduinos-whisper-worker/build.sh amd64
    bash anduinos-whisper-worker/build.sh arm64
    export ANDUINOS_VOICE_WORKER="$voice_root/anduinos-whisper-worker/obj/amd64/anduinos-whisper-worker"
fi
test -x "$ANDUINOS_VOICE_WORKER"
if [ -z "${ANDUINOS_VOICE_MODEL:-}" ]; then
    (cd anduinos-whisper-framework && bash scripts/download-model.sh)
    export ANDUINOS_VOICE_MODEL="$voice_root/anduinos-whisper-framework/obj/models/ggml-base.bin"
fi
echo "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe  $ANDUINOS_VOICE_MODEL" | sha256sum --check --status
if [ -z "${ANDUINOS_VAD_MODEL:-}" ]; then
    (cd anduinos-whisper-framework && bash scripts/download-vad-model.sh)
    export ANDUINOS_VAD_MODEL="$voice_root/anduinos-whisper-framework/obj/models/ggml-silero-v6.2.0.bin"
fi
echo "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987  $ANDUINOS_VAD_MODEL" | sha256sum --check --status

# Copy source into clean staging without touching developers' ignored caches.
# Keep staging for inspection; CI removes its disposable container afterwards.
voice_stage=$(mktemp -d -t anduinos-voice-ci.XXXXXXXX)
tar --exclude=obj --exclude=bin --exclude=__pycache__ --exclude='*.pyc' -cf - \
    anduinos-whisper-framework anduinos-whisper-gtk anduinos-whisper-worker \
    anduinos-control-panel anduinos-core-system anduinos-desktop \
    anduinos-desktop-core anduinos-desktop-apps | tar -xf - -C "$voice_stage"
export ANDUINOS_VOICE_SAMPLE="$voice_stage/anduinos-whisper-framework/data/benchmark/en-short.wav"
export PYTHONPATH="$voice_stage/anduinos-whisper-framework/src:$voice_stage/anduinos-whisper-gtk/src"
mkdir -p voice-test-results
export GSETTINGS_SCHEMA_DIR="$voice_stage/anduinos-whisper-framework/data"
glib-compile-schemas --strict "$GSETTINGS_SCHEMA_DIR"
(cd "$voice_stage/anduinos-whisper-gtk" && bash compile-locales.sh)
python3 -m unittest discover -s "$voice_stage/anduinos-whisper-framework/tests" -v 2>&1 | tee voice-test-results/framework.log
dbus-run-session -- env GSETTINGS_BACKEND=memory ANDUINOS_GTK_SMOKE=1 xvfb-run -a \
    python3 -m unittest discover -s "$voice_stage/anduinos-whisper-gtk/tests" -v 2>&1 | tee voice-test-results/gtk.log
node "$voice_stage/anduinos-whisper-gtk/tests/test_finishing.mjs"
dbus-run-session -- env GSETTINGS_BACKEND=memory ANDUINOS_ISOLATED_TEST_BUS=1 \
    python3 "$voice_stage/anduinos-whisper-framework/tests/integration/smoke-diagnostics.py"
python3 "$voice_stage/anduinos-whisper-framework/tests/benchmarks/benchmark-corpus.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --model "$ANDUINOS_VOICE_MODEL" > voice-test-results/cpu-corpus.json
python3 "$voice_stage/anduinos-whisper-framework/tests/benchmarks/stress-resident.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --model "$ANDUINOS_VOICE_MODEL" \
    --backend cpu --requests 100 > voice-test-results/cpu-stress.json 2> voice-test-results/cpu-stress-progress.log
python3 "$voice_stage/anduinos-whisper-framework/tests/benchmarks/benchmark-capture.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --model "$ANDUINOS_VOICE_MODEL" \
    --vad-model "$ANDUINOS_VAD_MODEL" --normalize-dbfs -26 > voice-test-results/capture-corpus-ci.json
python3 "$voice_stage/anduinos-whisper-framework/tests/benchmarks/benchmark-noise.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --vad-model "$ANDUINOS_VAD_MODEL" > voice-test-results/noise-shapes-ci.json
python3 "$voice_stage/anduinos-whisper-framework/tests/benchmarks/stress-vad.py" \
    --worker "$ANDUINOS_VOICE_WORKER" --vad-model "$ANDUINOS_VAD_MODEL" \
    > voice-test-results/vad-stress-ci.json 2> voice-test-results/vad-stress-ci-progress.log
echo "CPU acceptance passed; reports: $voice_root/voice-test-results; staging: $voice_stage"
