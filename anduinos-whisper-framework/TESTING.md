# Voice typing acceptance

Run from the repository root:

```sh
bash lib/test-voice-cpu.sh
```

The entry point builds the native worker for amd64 and arm64, verifies/downloads
the pinned Base and Silero VAD models, enables the native Python tests, runs the GTK/controller
and private-D-Bus diagnostic checks, compares all eight public clean/noisy
Chinese/English corpus cases against the CPU CLI, then runs 100 continuous CPU
requests plus cancellation/recovery. An assertion, missing dependency, native
failure or corpus accuracy regression fails the command. Arm64 is compiled,
not executed. No microphone or user dictation is used.

The same entry point also runs the normalized 32-case real DSP/streaming-VAD
frontend, 18 synthetic hum/fan-like/tapping noise-only cases, and two 30000-frame
VAD replays. Missing/unfinished phrases, false triggers and resource/replay errors
fail these gates. Frontend recognition error counts are reported separately:
the current frontend has known individual Chinese-long regressions relative to
unprocessed PCM, so a passing endpoint gate is NOT a claim of frontend accuracy
nonregression. Raw ASR CPU accuracy remains strictly compared with the CLI.

For testing an already extracted amd64 worker package and verified Base model:

```sh
ANDUINOS_VOICE_WORKER=/absolute/payload/usr/libexec/anduinos-whisper-worker \
ANDUINOS_VOICE_MODEL=/absolute/ggml-base.bin bash lib/test-voice-cpu.sh
```

These overrides skip building/downloading those inputs; they do not skip the
native runtime/corpus/stability gates. The private whisper library must remain
beside the worker in its packaged subdirectory. Reports go to the ignored
`voice-test-results/` directory. Source is copied to a temporary clean staging
directory, retained for debugging, so local ignored Python caches are untouched.

## CI environment

`voice-cpu-acceptance` requires an amd64 Docker executor with the Ubuntu 26.04
image. Dependencies are installed only inside that disposable container. The
explicit `/.dockerenv` guard rejects a shell executor before package installation
can touch a persistent runner host. A runner administrator must provision a
compatible executor if the existing runner is shell-only. Do not remove the guard
to make a shell job pass.

The worker publish job requires this gate; framework and GTK publication depend
on the worker through the existing package graph. The graph verifier explicitly
allows and requires this non-package edge, with regression tests against removal.
Reports are retained for 14 days even on failure. Container provisioning and an
actual remote pipeline remain to be validated; local script execution is not
evidence that GitLab's Docker executor or network dependencies work.

## Hardware coverage and limits

### Laptop timing handoff

After installing the new packages and loading the new desktop extension, keep
the same model and language for a short comparison. Do not change several
settings at once or infer performance from the processor name.

1. With live preview off, start dictation and let any first-use measurement and
   preparation finish. Say a short sentence, pause, and wait for insertion.
2. Repeat twice in that session, without stopping the service, to compare warm
   requests with initial preparation. Note whether the delay is before Listening,
   after speech ends, or after Recognizing finishes.
3. While the service is still running, use Settings → Performance diagnostics →
   Export, or run `anduinos-voice-diagnostics` to print the same sanitized JSON.
   The export does not start a stopped service or contain speech/text. Export
   before closing the session; diagnostics are bounded and kept in memory.
4. If needed, repeat with CPU selected manually, preserving model, language and
   microphone. Export a second report and label the two configurations. Restore
   Automatic afterwards. Test live preview separately only after this baseline.

Include package versions, selected model/language/backend, preview setting, and
cold/warm context alongside the reports. An absent desktop delivery ACK does
not mean zero insertion latency; endpoint timing is relative to the detector's
last positive frame, not a ground-truth human speech boundary. These reports
can locate initialization, queueing, inference or delivery delays without asking
the user to share a recording or transcript.

The worker recommends `libggml0-backend-vulkan` so standard APT installs include
the optional GPU plugin. A compatible system Vulkan driver is still needed;
this package does not install or replace proprietary display drivers. Installing
without recommendations remains supported as CPU-only operation. Automatic
selection measures the available backend rather than assuming a plugin or GPU
name proves useful acceleration.

CPU-only accuracy is a mandatory gate. CPU timings are observations; a shared
runner's speed is not a laptop latency promise. Stress acceptance checks repeated
output, unchanged worker PIDs, descriptor/thread counts, a 64 MiB post-warm-up RSS
growth ceiling, bounded cancellation and child cleanup. This finite run does not
prove indefinite leak freedom.

GPU acceptance must be run separately on actual hardware with
`scripts/benchmark-corpus.py --gpu --worker ... --model ...`. Device discovery or
a GPU request that falls back to CPU is not GPU coverage. The existing developer
GPU corpus has a known noisy-Chinese mismatch; automatic selection rejects
output-changing candidates. Raw GPU corpus and selected-policy accuracy are
distinct gates. Neither workstation results nor this CI certify Lunar Lake.

Run `scripts/benchmark-selected.py --worker ... --model ...` to check the actual
automatic selection, not only the fixed four-thread CPU configuration. It uses
a temporary calibration cache and tests auto-language, English and Chinese on
all relevant short/long clean/noisy samples against the matching-language CPU
CLI. Calibration falling back without a measured result, a different actual
backend, or increased error counts fails this separate gate. Do not run timing
benchmarks concurrently with other CPU-heavy builds or benchmarks.

## Public-fixture capture frontend investigation

Run `scripts/benchmark-capture.py --worker ... --model ... --vad-model ...` from the framework
directory to exercise actual optional WebRTC DSP, private-pipe Silero VAD and endpoint logic,
then recognize its emitted chunks with the CPU worker. This does not open an
input device. Four conditions (clean, 20 dB SNR, 10 dB SNR and quieter 20 dB SNR)
run with noise reduction off and on; stationary noise continues during pauses.
The report compares raw recognition errors with frontend recognition errors,
without exporting transcripts. Nonzero exit means no phrase or an unclosed
phrase after trailing room tone; `accuracy_nonregression` is a separate reported
result, not implied by a zero exit code. Inspect both. Endpoint timestamps are
audio-timeline observations; inference wall time is measured separately.
This synthetic corpus does not cover all real microphones or environmental noise.
Use `--normalize-dbfs -26` to explicitly normalize speech RMS before applying
conditions (its quiet variant is then -46 dBFS). Source FLEURS recordings have
very different original levels; reports disclose both original and input levels.
`--reference-webrtc` retains the former detector only as a developer comparison,
not as a production fallback. It requires a WebRTC build with working built-in VAD.

Silero v6.2.0 is a pinned, MIT-licensed 885098-byte model bundled by the framework;
runtime does not download it. The same private whisper.cpp library runs it on one
CPU thread in a separate `--vad` worker. ASR model selection is unchanged. Startup
runs in preparation with the microphone off. Capture uses bounded 32 ms frames;
failure stops capture rather than silently losing recurrent state mid-sentence.
Microphone level testing does not require VAD or ASR initialization.

`scripts/benchmark-vad-stream.py --library ... --vad-model ... --worker ...`
compares the actual streaming pipe with upstream whole-clip probabilities;
omitting `--worker` tests the direct native export instead. The ctypes binding is
developer-only and never loaded into the desktop service. `scripts/stress-vad.py
--worker ... --vad-model ...` runs 30000 frames twice (16 simulated audio minutes
per replay), checking identical recurrent output, stable descriptors/threads,
bounded RSS growth and child cleanup. Faster-than-real-time processing is not a
wall-clock soak test or a guarantee on other CPUs.

## Isolated real desktop smoke test

From the repository root, run:

```sh
python3 anduinos-whisper-gtk/scripts/smoke-shell.py --payload /absolute/extracted-packages
```

Requires local GNOME Shell with headless Wayland support and usable rendering.
The payload must contain the worker, framework (including Base model) and GTK
packages. The test uses a separate bus, virtual monitor, runtime/config/cache/data
directories and memory settings. Isolation is applied before launching the bus,
so activated services also inherit the temporary paths. It never replaces the
current Shell or opens a microphone; a verified public audio fixture replaces
capture. Recognition, service authorization, Shell extension, clipboard/keyboard
dispatch and GTK text reception remain real.

It checks that stopping retains an in-flight final result, the public words reach
a focused text view, delivery is acknowledged, a non-Shell Start is denied, and
dismissing cancels a second request without inserting text. Temporary logs remain
for debugging. A passing run does not validate physical microphone capture or
every desktop application, and its synthetic endpoint metric is not a microphone
latency measurement. Ordinary CI does not claim this hardware/rendering test.
