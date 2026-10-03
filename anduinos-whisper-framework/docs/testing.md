# Voice typing tests

Run commands below from the repository root. Tests use licensed public fixtures
and synthetic noise, never a microphone or private dictation.

## Profiles

Run from the package directory with the local or published Apkg CLI that supports
`TestCommand`. CI selects only `anduinos-package-release-test`.

| Package/profile | Coverage and prerequisites |
| --- | --- |
| Framework / `anduinos-package-release-test` | Rust behavior, synthetic audio/DSP, private-D-Bus service and diagnostics, plus shipped Python GTK helpers; Rust/Cargo and GLib/GStreamer development libraries, Python GI, GStreamer base/good/bad, gettext and D-Bus |
| GTK / `anduinos-package-release-test` | Settings/controller behavior and Shell isolation guards; Python GI, Node and gettext |
| GTK / `gui` | Real widgets with temporary schemas, memory settings and Xvfb; GTK/Adwaita, Xvfb, xauth and D-Bus |
| Framework / `voice-native` | Native resident worker, CLI accuracy comparison and VAD with public audio; supplied models, whisper-cli and coreutils timeout |
| Framework / `voice-cpu` | Rust/CLI corpus accuracy, 100 resident requests, Rust capture/noise and two 30,000-frame VAD replays; native fixtures plus whisper-cli |
| GTK / `desktop-voice` | Real headless GNOME Shell, source extension, native recognition and GTK insertion; usable rendering and native fixtures |

No profile generates, extracts or installs a deb. Required missing tools or
fixtures fail the selected entry. GUI/model tests excluded by profile are not
reported as successful coverage by the release lane.

## Backend checks

Run from `anduinos-whisper-framework` with native build dependencies installed:

```sh
cargo test --locked
cargo test --locked --test native_runtime -- --ignored --nocapture --test-threads=1
cargo test --locked --lib native_capture -- --ignored --nocapture
cargo test --locked --test service_bus -- --include-ignored
```

The opt-in Rust native/capture checks accept `ANDUINOS_VOICE_WORKER`,
`ANDUINOS_VOICE_MODEL` and `ANDUINOS_VAD_MODEL`; if omitted, they use the installed
worker and build models under `obj/models`. Service preparation smoke tests use
installed model/calibration paths. They do not open a microphone. Capture checks replay public English
and Chinese audio through appsrc, the production DSP/VAD/segmenter, then ASR.
The bus tests use an inaccessible private PipeWire socket and compare the live
interface against `tests/support/voice-typing.xml`.
The Rust corpus gate compares recognition errors directly against whisper-cli
with the same model, CPU threads and decoding defaults: 16 clean/noisy public
audio cases across automatic/English/Chinese language modes. English uses word
errors and Chinese uses character errors. Both paths must produce nonempty
output, and Rust must not have more errors than the CLI. The gate needs
`timeout` from coreutils to bound each CLI invocation.
The previous Python backend and migration-only benchmarks are retired;
`rust-migration.md` retains the historical acceptance record.

For native profiles, build the worker from source for the test host and prepare
licensed model fixtures first. Supply absolute paths:

```sh
export ANDUINOS_VOICE_WORKER=/absolute/source-build/anduinos-whisper-worker
export ANDUINOS_VOICE_MODEL=/absolute/fixtures/ggml-base.bin
export ANDUINOS_VAD_MODEL=/absolute/fixtures/ggml-silero-v6.2.0.bin
export ANDUINOS_VOICE_SAMPLE=/absolute/source/anduinos-whisper-framework/data/benchmark/en-short.wav
# Optional: use an unpacked/source-built CLI instead of /usr/bin/whisper-cli.
export ANDUINOS_WHISPER_CLI=/absolute/fixtures/whisper-cli
apkg test --profile voice-native
apkg test --profile voice-cpu
```

Preserve any private library layout required by the source-built worker.
Profiles do not silently build another architecture or download models. The CPU
entry tests this package only; it does not copy other packages or repeat their
release/GUI suites. CPU reports go to the ignored `obj/voice-test-results/` directory:
`rust-cpu-native.log` (including per-case corpus error counts) and `rust-capture-noise.log`.
These benchmarks require an idle machine and do not certify GPU
inference or remote runner provisioning.

## Interpretation and limits

- Raw ASR accuracy is gated against the independent CLI. Capture tests gate
  phrase completion and noise rejection; they do not prove universal accuracy
  after frontend processing.
- GPU detection or a GPU request falling back to CPU is not hardware coverage.
  Output-changing candidates must be rejected by automatic selection. Raw GPU
  accuracy and selected-policy accuracy are distinct checks.
- Endpoint timing starts at the last VAD-positive frame, not a ground-truth
  human speech boundary. Inference wall time is measured separately.
- Host RSS does not measure VRAM. Faster-than-real-time VAD replays are not
  wall-clock soak tests. Noise shapes are synthetic, not environmental
  recordings; competing speakers require separate real-world evaluation.
- Workstation results do not certify Lunar Lake or other laptop CPUs/drivers.
  Keep model and decoding quality constant when comparing configurations.

## Desktop integration

Build the uninstalled Rust fixture service first; its opt-in feature replaces
microphone capture with public PCM, while retaining the real service/worker path:

```sh
cargo build --manifest-path anduinos-whisper-framework/Cargo.toml --locked \
  --features test-support --bin voice-fixture-service
export ANDUINOS_VOICE_SERVICE="$PWD/anduinos-whisper-framework/target/debug/voice-fixture-service"
apkg test --path anduinos-whisper-gtk --profile desktop-voice
```

Requires GNOME Shell with headless Wayland support and usable rendering.
Supply the native artifact/model environment variables above. The test loads
the source extension and the explicitly supplied Rust fixture service. It
uses a private bus, virtual monitor, temporary settings/cache/runtime and public
audio in place of capture. It never replaces the current Shell or opens a mic.
Recognition, authorization, clipboard/keyboard dispatch and GTK reception are
real. Temporary desktop state is cleaned up after execution. Finish must retain
the final result; Dismiss must prevent late insertion.
Passing does not validate every desktop application or physical microphone.

## Laptop timing report

1. Keep the model/language/microphone fixed and turn live preview off. Let
   initial calibration/preparation finish, then speak a short sentence.
2. Repeat twice in the same session to compare warm requests. Note whether the
   delay is before Listening, after speech ends, or after Recognizing finishes.
3. Before closing the service, export from Settings → Performance diagnostics,
   or run `anduinos-voice-diagnostics`. Reports are bounded and memory-only;
   exporting does not start a stopped service.
4. If needed, repeat with manual CPU and export separately. Preserve the other
   settings; restore Automatic afterwards. Test live preview separately.

Include package versions, selected model/language/backend and cold/warm context.
No recording or transcript is needed. Missing desktop delivery acknowledgement
does not imply zero insertion latency.
