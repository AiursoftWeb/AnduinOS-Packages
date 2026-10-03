# Rust backend migration

The backend service and diagnostics CLI are implemented in Rust. The Python GTK
frontend, Shell extension, D-Bus activation, GSettings schema, models and native
whisper.cpp worker are retained. The implementation summary and acceptance
checks below describe the historical migration baseline.

Production Python code consists of six GTK compatibility modules. The old
Python backend and migration-only comparison tools have since been removed.
The results below record the migration-time checks, not the current test count.
Current commands and gates are maintained in `testing.md`.
Normal builds do not enable the `test-support` feature or install its fixture
service. Production process launches are the native worker and optional audio
cue player, never a Python backend or per-phrase CLI fallback.

## Compatibility audit

| Requirement | Implementation and verification |
| --- | --- |
| Same D-Bus name, object, methods, signals and parameter signatures | `service.rs`; isolated-bus test compares live introspection with the frozen reference definition, including argument directions/types. The contract is now retained as standalone XML. |
| Only current Shell owner controls dictation; clean ownership lifecycle | Private-bus tests reject all five protected methods from another connection and verify duplicate instance, Quit, Shell loss and absent Shell. Service timer preserves the 300-second idle condition. |
| Stop cancels; Finish drains accepted finals; stale output cannot cross sessions | Runtime queue tests cover eight retained finals under overload, preview replacement/invalidation and cancellation. Service-state test covers preparation cancellation, countdown expiry, no-speech restoration, test capture, missing model and stale events. Headless desktop test verifies Finish insertion and Dismiss suppression. |
| Native audio capture, metering, VAD, DSP and watchdog | `audio.rs` retains PipeWire/GStreamer S16LE mono 16 kHz capture. Synthetic tests cover pipeline/meter/stop/watchdog; public English/Chinese replay traverses actual DSP, VAD and segmenter into ASR. Eighteen noise-shape/level/DSP cases produce no false dictation. |
| No microphone during tuning | Preparation precedes capture construction. Native tuning uses pinned public fixtures; private-bus cancellation tests have no reachable PipeWire socket. |
| Framing, cancellation ACK, timeout, child lifetime and GPU fallback | Transport tests cover fragmented/buffered/malformed responses, shared startup/inference deadline and ACK-only reuse. Native stress verifies persistent results/resources, cancellation recovery and reaping. Session tests cover model identity, 180-second eviction and one GPU-to-CPU retry without quality changes. |
| Automatic tuning, bounded budgets/cache, settings generations and previews | Tuner tests cover 10/60-second budgets, warm scoring, CPU preference for small gains, fake GPU/quality-regression rejection, cancellation, private bounded cache and generation invalidation. |
| Transcript cleanup, Chinese script, punctuation, voice commands and cues | Command tests and real OpenCC checks pass; 16 clean/noisy corpus cases in auto/English/Chinese match the Python backend exactly. Runtime punctuation/commands remain settings-controlled; audio cue code retains the opt-in setting. |
| Bounded private diagnostics, delivery tickets, no auto-start export | Unit tests exercise allowlists, malformed reports and tickets; private-bus CLI check verifies absent-service behavior; desktop test reports delivery acknowledgement. No PCM/transcript is included in diagnostics. |
| Preserve GTK frontend API | Only compatibility helpers are packaged; all 26 GTK tests pass both from source and against extracted package helpers. Actual Shell-to-GTK insertion passes. Frontend product source and extension are unchanged. |
| Real amd64/arm64 packages, activation and attribution | Both architecture builds pass; ELF binaries and six helper modules inspected, no Python daemon or fixture service shipped. Extracted amd64 passes service/native-cancellation smoke. ARM ELF/QEMU startup verified. Build dependencies/MSRV are documented and checked; upstream crate license texts are collected from locked sources into the deb. |

## Historical migration results

The following results were recorded before retiring the Python reference.
Current tests compare Rust directly against whisper-cli and the independent
D-Bus XML contract; retired Python suites are no longer release requirements.

- Regular Rust suite: 39 library tests and the private-bus integration test pass.
  Native tests are explicitly opt-in, not counted as passing from ignored output.
- Python migration reference: 130 tests and 14 native reference tests pass;
  GTK: 26 tests pass. Both packages' release test profiles pass.
- `cargo test --test service_bus -- --include-ignored`: both tests pass,
  including real native preparation interrupted by Finish/Stop and child cleanup.
- CPU qualification (`tests/run-cpu.sh`) passes with:
  - CLI/reference corpus accuracy nonregression;
  - Rust/Python equality for all 16 clean/noisy corpus cases;
  - 100 measured resident requests after warmup, stable PID/FD/thread counts,
    RSS growth below 64 MiB, cancellation under two seconds and recovery;
  - two deterministic 30,000-frame VAD replays (960 simulated seconds each),
    stable resources and less than 16 MiB post-warmup RSS growth;
  - English/Chinese phrase completion plus all 18 noise combinations.
- A three-run idle comparison of extracted packages measured Python RSS 41,344 KiB /
  PSS 24,710 KiB versus Rust RSS 9,012 KiB / PSS 3,429 KiB. Median observed
  startup was 78.87 ms versus 6.20 ms. This excludes loaded model memory and is
  not a model inference speed claim.

## Intentional differences and limits

- With the `small` model, the 10-second quick calibration can exhaust its
  budget on CPU measurements before testing GPU. This also occurred with the
  Python reference. Full calibration is available through Retest in the frontend.
- Startup and inference now share one deadline. The old Python transport could
  consume the full timeout separately for each phase.
- The Rust cache fingerprint has its own encoding tag. Existing Python
  performance measurements are recalibrated, not silently interpreted as Rust
  measurements. User model/backend/thread preferences are preserved.
- Native test results apply only to the tested backends; they do not certify
  all GPUs, microphones, desktop applications or ARM hardware.
- VAD replay runs faster than real time; it is not a 32-minute wall-clock soak.
  Synthetic noise does not represent every room or competing speaker.
- Full raw corpus equality does not imply perfect recognition; the independent
  CLI baseline still has transcription errors. Endpoint replay alone does not
  prove universal accuracy after all audio preprocessing.
- Build tooling never installs system packages or changes the running service.
  Build environments must provide the documented development dependencies.
- Architecture package builds must run serially in one checkout because Apkg
  builds share staging directories.
