# Voice typing performance work — implementation and acceptance ledger

This is an in-progress implementation, not a release-readiness declaration.
The source now uses a persistent worker via SessionEngine and prepares an
automatically selected configuration before microphone capture. Manual controls
are implemented; full acceptance gates below are not complete. Nothing has been installed.

## Required end state

User clarification: voice typing has never been released. There is no shipped
client/protocol compatibility requirement. Prefer one coherent first-release
interface over legacy adapters. Reliability fallback remains necessary.

- One whisper.cpp engine, CPU fallback, optional supported GPU acceleration;
  no per-laptop model table and no mandatory NPU dependency.
- Private, isolated persistent worker: reuse a model, reload when its model or
  configuration changes, release after idle, cancel, restart after crash/timeout.
- Small local benchmark using bundled audio, not microphone recordings. Measure
  cold startup separately from repeated warm inference. Compare available CPU
  thread counts and GPU candidates using identical model/decoding parameters.
- Cache selection with engine/driver/configuration invalidation; manual backend
  and thread override, explicit retest and restore-default controls. Never switch
  model quality silently for a better performance score.
- Allow-listed diagnostic export with endpoint delay, queue delay, initialization,
  encode/decode time, delivery latency, backend and memory. No audio/transcripts,
  arbitrary native logs, private paths, or prompts in reports.
- Fixed licensed Chinese/English, short/long, clean/noisy regression corpus with
  accuracy and latency comparisons, memory and stability checks. CPU-only runs
  are mandatory, real GPU checks separately identified, unavailable hardware is
  never presented as validated.
- Verify packaging/builds and preserve existing finishing, cancellation, password
  protection, bounded queue and text-insertion behavior.

## Historical evidence (superseded where later entries say otherwise)

The entries below record the development sequence, not the current architecture
or release status. In particular, the final worker uses a private pinned
whisper.cpp library, has no production CLI fallback, and automatic selection is
wired into preparation. See the newest entries under Remaining work.

- CLI timing extraction, bounded in-memory allow-listed history, read-only
  `GetDiagnostics`, and an export command that does not auto-start recording.
- Native `../anduinos-whisper-worker/src/worker.c` compiles with warnings-as-errors for amd64 and arm64.
  It uses distro libwhisper via checked symbols/version and inherited pipes;
  it creates no network listener, disables core dumps and dies with its parent.
- Python transport implements framed PCM, bounded responses, cooperative abort,
  timeout kill/reap and restart on next request. It is now wired into the daemon.
- Separate worker debs build for amd64 and arm64 using SHA-256 pinned Ubuntu
  development headers and the existing C cross toolchain. CI dependency ordering
  validated. Tests executed the binary extracted from the amd64 deb, not only a
  separately compiled prototype. The arm64 binary is built, not runtime-tested.
- SessionEngine reuses the same configuration/model, reloads on model file or
  configuration changes, releases after 180 seconds idle, and bounds GPU-to-CPU
  fallback to one retry. Unsupported worker ABI falls back to CPU CLI, disclosed
  in diagnostics. No model-quality fallback. Tests cover cancellation, lifecycle,
  fallback and error recovery; framework 58 and GTK 22 tests passed at this stage.
- BackendTuner and AutomaticSelector now implement a small CPU 2/4/8-thread
  candidate set (bounded by affinity), a GPU candidate, cold plus two warm runs,
  a 60-second overall measurement budget, output-consistency gating, private
  atomic cache and environment fingerprint. Driver/library/model/config changes
  invalidate selection. These components are tested but not yet connected to
  daemon preparation or settings UI. Default capture still uses SessionEngine CPU.
- Four FLEURS CC-BY-4.0 validation excerpts are bundled with provenance and hashes:
  English/Chinese short/long, plus deterministic in-memory white-noise variants.
  `scripts/benchmark-corpus.py` compares native CPU/GPU against CPU CLI using WER
  and CER, same Base model and four threads, and returns nonzero on regressions.
- IMPORTANT: the new real-corpus accuracy gate currently FAILS. Native CPU gained
  one error on clean zh-long versus CLI (1 vs 0), though noisy zh-long improved
  (2 vs 3). GPU had one additional error on noisy zh-short (1 vs 0) and clean
  zh-long (1 vs 0). English cases were zero errors. Do not widen the gate or drop
  samples to conceal this. This is an unresolved implementation/engine-state
  investigation, not a successful release test.
- Experiment `--no-fallback` disables temperature fallback only in the native
  worker. It is NOT the application default and did NOT fix those regressions.
  Four repeated CPU zh-long calls: fresh CLI stayed correct; reused native
  returned the homophone 独者 instead of 读者 on the fourth call. Native RNG/state
  reuse and decoder behavior need further investigation; no cause proven yet.
- Further controlled experiments also fail the unchanged per-sample accuracy
  gate: beam 1 worsens noisy zh-short (1 vs 0) and zh-long (5 vs 3); beam 4 fixes
  clean zh-long but worsens noisy zh-long (CPU 4/GPU 5 vs CLI 3); disabling flash
  attention worsens noisy zh-short on both backends and does not fix clean
  zh-long. None has been adopted as a default. Experimental command switches
  are recorded in the benchmark JSON; do not confuse these with shipped policy.
- Repeated zh-long CPU inference returned the same fourth-call homophone with
  temperature fallback disabled, excluding RNG fallback alone as an explanation.
- Endpoint telemetry now flows from AudioCapture through the queue into history.
  It measures captured-audio time since the last VAD-positive frame, labels
  silence/max-duration/manual-finish separately, and does not conflate this
  estimate with exact human speech end or model processing time. Dedicated tests
  verify all three reasons and audio-timeline durations.
- DeliveryTicket + Shell-only ReportDelivery now link final inference completion
  to compositor paste dispatch. Old clients still receive the unchanged Transcript
  signal. Duplicate/evicted tickets cannot mutate history. This is dispatch plus
  acknowledgement latency, NOT proof that the destination app displayed text.
  The Shell also rechecks password/PIN purpose and focus after the paste delay.
- Settings now offers async report export via a native save chooser. It uses
  NO_AUTO_START, revalidates the diagnostic allow-list at the client, writes only
  after destination selection with PRIVATE/REPLACE_DESTINATION, and does not save
  anything on chooser cancellation. Chinese translations and export regression
  tests added; real GTK introspection signatures checked. Full rendered UI smoke
  test still pending. GTK version is now 2.0.2-21, framework minimum 2.0.2-12.
- Real amd64 Base-model integration tests cover CPU process reuse, actual backend
  reporting for GPU candidates, cancellation followed by successful recognition,
  timeout cleanup and crash recovery. No microphone opened during these tests.
- Public JFK sample on the current i9-13900KS/RTX 4090 workstation: CPU/4 threads
  three warm requests approximately 1.06–1.20 s; GPU first request 5.03 s and next
  two 0.25/0.48 s. These are exploratory observations, not portable thresholds or
  Chinese/laptop acceptance results. First-use compilation must not be confused
  with steady-state latency.

## Remaining work

Streaming VAD integration checkpoint (2026-09-07, about 07:20 UTC):

- Previous goal turn was concrete progress (GPU recovery fix, actual frontend
  evidence, verified batch/stream VAD), not a wait or blocker. This turn also
  changes production source and obtains native/package/desktop evidence.
- worker.c now supports private `--vad MODEL` mode with exactly 1024-byte S16LE
  frames, CPU one thread, probability-only JSON, ready mode check, PDeath/core
  protections and no audio files. Uses the same pinned private library/streaming
  exports. Both amd64 and arm64 builds and apkg build --all PASSED; ARM remains
  compile-only, not hardware tested.
- Shared WorkerTransport factors the existing ASR bounded pipe/close lifecycle.
  New vad.py VadEngine start/classify/close has a 1-second exchange bound,
  strict numeric finite probability validation, and never silently restarts
  recurrent state in mid-capture. Nine unit/native tests passed, including quiet
  English, deterministic restart, no trigger on silence, crash, SIGSTOP timeout,
  cancellation, child cleanup and malformed response rejection.
- Added hash-pinned build-time download-vad-model.sh, VAD-NOTICE/VAD-LICENSE
  (upstream Silero MIT, exact text verified), and framework manifest model/license
  inclusion. Download and packaged model SHA verified. No runtime download.
- Production AudioCapture now runs optional WebRTC DSP WITHOUT its old built-in
  VAD, accumulates 32 ms frames for isolated Silero classification, explicitly
  passes decisions into endpoint logic, preserves the <32 ms tail on Finish,
  drops it on Cancel and closes the detector after the streaming thread stops.
  Old GStreamer voice-message path removed from production; only developer
  --reference-webrtc retains the old comparison implementation.
- Daemon initializes VAD on the recognition/preparation thread before opening
  the microphone. Explicit prepared_vad ownership handles cancellation during
  startup and between ready/GLib handoff. Settings microphone level testing skips
  VAD/ASR initialization. Startup metrics are allow-listed as engine=vad. Added
  tests for handoff, cancel-during-start, stale callbacks, frame ordering/tails,
  optional DSP and actual pure-noise GStreamer+VAD processing. 31 focused tests
  pass (one native opt-in skip when run without env; native run tested it).
- Real private-pipe equivalence PASSED on 12996 frames/36 cases; compared to
  upstream batch probabilities (<=1e-6 difference). Median 0.082427 ms, P95
  0.088167 ms, max 0.651136 ms per 32 ms frame on workstation. Report
  voice-test-results/silero-pipe-corpus.json. No claim about Lunar Lake speed.
- New actual DSP + streaming pipe + ASR normalized 32-case run PASSED endpoints.
  Errors are 31 vs raw 30 (former WebRTC frontend was 83 on SAME matrix).
  Report capture-silero-normalized.json retains accuracy_nonregression=false:
  individual Chinese-long cases still regress. Public clean example is a
  homophone substitution 读者 -> 独者, not a missing prefix; do NOT hard-code a
  fixture-specific correction. Raw ASR model/decoding remain unchanged.
- stress-vad.py PASSED: two deterministic 30000-frame replays, 16 simulated
  audio minutes each, wall 5.13 seconds, RSS growth 0.0 MiB, maximum RSS108.17 MiB,
  one worker thread, P95 frame 0.085943 ms, both children reaped. Faster-than-real-
  time playback is not a real-time soak. Report vad-stress.json/progress log.
- Unified CPU acceptance with integrated VAD PASSED in
  /tmp/anduinos-voice-ci.n5zmY0b7: 133 framework tests, 33 GTK, native ASR/VAD,
  Node, private D-Bus, CPU corpus and 100-request ASR stress (session 89114 exit0).
  CI entry now verifies/downloads ANDUINOS_VAD_MODEL and enables native VAD tests.
- All three packages rebuilt. NEW authoritative combined extracted payload:
  /tmp/anduinos-vad-packages.dfX5BsOq/payload. Worker debs in original worker/bin;
  framework/GTK debs under /tmp/anduinos-vad-packages.dfX5BsOq/{package}/bin.
  Framework2.0.2-12, GTK2.0.2-21, worker2.0.2-1 unchanged within uncommitted batch.
  Private library cmp matches tested build, VAD hash matches pinned upstream.
- Actual isolated GNOME end-to-end PASSED on these VAD packages:
  /tmp/anduinos-shell-e2e.ulxx_mla, 11 public words inserted, delivery36.752ms,
  Stop preserved final and Dismiss inserted nothing. Harness now uses actual
  packaged VAD preparation and closes it in its public capture substitute.
  Physical microphone remains substituted, not tested. Session4322 exit0.
- Then removed repeated fixture decoding on healthy same-config session Start:
  SessionEngine.prepare() warms only new/reloaded workers, checks actual child
  liveness, renews idle time, restarts a dead idle child, respects cancellation.
  Three new unit cases plus native reuse guard added; 13 lifecycle tests pass.
  Measured same worker cold preparation1042.52ms vs warm0.041ms, no second decode
  (prepare-reuse.json). These are ONLY preparation timings, not full UI readiness.
  Framework rebuilt again and extracted; current payload contains this change.
  Latest framework build session6076 exit0; no active package builds remain.

CURRENT LIVE RUN: session85751 is unified CPU acceptance on the latest source
and NEW extracted payload worker/Base/VAD, staging
/tmp/anduinos-voice-ci.V6zo145w. Poll this SAME handle; do not restart based on an
observation timeout. Initial native tests were running at checkpoint. Do not
modify lib/test-voice-cpu.sh while its live shell runs.

NEXT: finish that run; rerun actual GNOME smoke after warm-preparation change;
check selected-policy and GPU evidence against final binary as appropriate;
consider additional non-speech noise shapes (current noise tests are stationary
Gaussian, not every environment). Need complete original requirement audit,
honest remaining accuracy/physical-mic/Lunar Lake/remote-CI limits and packaging
handoff. Do not claim all frontend cases have zero regression. No commit, push,
host installation or user microphone recording has been performed/authorized.

Latest quality investigation (2026-09-07): user explicitly allowed at least three
hours for quality work, not a rushed release. No commit/push/install authorized
or performed in this goal. The previous status turn verified completion of CPU
acceptance, so it yielded new evidence rather than an unverified wait.

GPU revalidation CPU acceptance /tmp/anduinos-voice-ci.XrvMG7cB PASSED (exit 0):
116 framework tests including native execution, 33 GTK tests, Node, private bus,
CPU corpus and 100-request stress. After this run, added begin_measurement() to
close the old resident model BEFORE benchmarking, preventing old RAM/VRAM usage
from biasing selection or causing a false GPU OOM. Cached/manual selections keep
the healthy model. Focused 26 service/noisy tests pass after that addition. Both
framework runtime changes still need a fresh packaged rebuild and final checks.

Real capture experiment found an important remaining frontend weakness:
benchmark-capture.py exercises real WebRTC DSP/VAD and actual AudioCapture
endpoint processing, not forced voiced flags. Source English fixtures have RMS
-62.47/-63.29 dBFS; Chinese -40.80/-44.63 dBFS. Source-level English never triggers
WebRTC VAD, although ASR recognizes it. Report voice-test-results/capture-corpus.json
exits 1 (20 of 32 conditions fail to emit/finish). Do not claim the microphone
frontend passed based on earlier raw-PCM engine benchmarks.

Explicit --normalize-dbfs -26 run separates fixture amplitude from SNR; its quiet
condition is -46 dBFS. All 32 conditions trigger and finish, but summed frontend
errors are 83 vs 30 raw errors. Quiet speech loses portions during VAD trimming.
Report capture-normalized-corpus.json has endpoint_passed=true and
accuracy_nonregression=false. Reports disclose normalization and input/source
levels; original failing evidence is retained. These are synthetic conditions,
NOT evidence of the user's physical laptop's cause. Two fixture/DSP-silence tests
pass. TESTING.md explains endpoint exit status is not an accuracy claim.

Evaluated Silero v6.2.0 via the EXISTING private whisper.cpp 1.8.3 implementation
(no ONNX/PyTorch, no new ASR backend). Upstream model is MIT, pinned HF revision
c5c26827b67dfd053856f92e824e14fdcc123daf, file ggml-silero-v6.2.0.bin, 885098 bytes,
SHA256 2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987.
Downloaded/verified ONLY at /tmp/anduinos-vad-probe.xjNEGHN1/ggml-silero-v6.2.0.bin.
It has NOT been bundled into packages or enabled in production. Primary sources:
https://huggingface.co/ggml-org/whisper-vad/tree/c5c26827b67dfd053856f92e824e14fdcc123daf
https://github.com/snakers4/silero-vad/blob/master/LICENSE
Pinned local upstream obj/state-metrics/whisper.cpp lines 5086 onward show that
the public batch detect_speech entry resets recurrent state on EVERY call; it
cannot be reused naively for live 32 ms frames.

Developer-only scripts/benchmark-vad.py uses a version-checked exact-header
ctypes binding outside the desktop service. Initial probe aborted because it
omitted ggml_backend_load_all(); fixed that setup and disabled core dumps. Final
batch run PASSED endpoint behavior in all 36 source/normal/quiet x clean/20/10 dB
cases: aggregate errors 44 vs raw 43, all three 15-second noise-only tests
(sigma 50/580/3000) have zero voiced frames. This is NOT a blanket accuracy pass,
and its 36-case matrix differs from WebRTC's 32-case processing-on/off matrix.
Reports silero-batch-corpus.json and silero-batch-progress.log; session 94428
completed exit 0, no longer running. Whole-clip VAD time 32–54 ms on workstation.

Added EXPERIMENTAL native streaming exports to worker/src/state-metrics.cpp:
anduinos_vad_stream_open/process/close_v1. They use the same pinned graph/model,
CPU one thread, fixed 512 samples, retain LSTM state and graph, reject wrong frame
size, and expose no C++ layout to consumers. Existing ASR code remains unchanged.
Built amd64 private library with build-state-metrics.sh (session 72675 exit 0),
at worker/obj/amd64/anduinos-whisper/libanduinos-whisper.so.1. Packaged payload
/tmp/anduinos-upper-packages.WGYigm still contains the PRE-experiment library.

scripts/benchmark-vad-stream.py verified the native streaming export against
upstream batch on all 36 cases: 12996 frames, maximum probability difference 0.0,
median 0.076591 ms, P95 0.079619 ms, max 0.114752 ms per 32 ms frame. This excludes
process transport/GStreamer and does not represent Lunar Lake. Report
silero-stream-corpus.json; session 19623 completed exit 0. No live experiment,
benchmark or build processes remain at this checkpoint.

NEXT: this evidence warrants evaluating integration of streaming VAD into a
private isolated worker mode and actual capture. Keep one inference implementation
and CPU-only VAD; do not put ctypes/native model execution in the desktop service.
Need bounded pipe transport, startup/cancel/teardown, model/license packaging,
frame accumulation, actual DSP+streaming+ASR comparison, noise false-positive
tests and CPU cost/stability before replacing current WebRTC VAD. Production
capture is STILL unchanged; don't claim the new VAD is delivered. All additions
are local and uncommitted. Complete original requirement audit remains required.

GPU revalidation fix: SessionEngine now has explicit invalidate(), called after
successful fresh calibration (including driver updates) and manual retry of a
previously failed GPU. It clears the failure memo AND the loaded worker so equal
model/backend/thread keys cannot preserve obsolete libraries or a sticky CPU
fallback. Cached/ordinary starts preserve healthy model reuse; idle cleanup does
not reset a known GPU failure. Ten lifecycle and 26 noisy-dictation/service tests
passed, including eight selection/reset combinations. Unified CPU acceptance is
running on this source in /tmp/anduinos-voice-ci.XrvMG7cB (session 6554).

The preceding unified CPU run /tmp/anduinos-voice-ci.xUMIH07g completed with exit
0, including 33 GTK tests, real native transport tests, CPU corpus and 100-request
stress. It predates the GPU revalidation fix and is not its final acceptance.

Added benchmark-capture.py to investigate the real DSP/VAD/endpoint frontend,
which previous raw PCM inference benchmarks bypassed. Public fixtures include
continuous room-tone padding, clean/20 dB/10 dB/quiet conditions and processing
off/on. No physical microphone, no EOS flush masking endpoint failures, no
transcript export. Two stimulus/real-DSP silence tests pass; full speech results
are pending. This is additional evidence, not a laptop simulation claim.

Final desktop harness rerun with process-group cleanup PASSED in
/tmp/anduinos-shell-e2e.c_kmdap6: 11 public words actually inserted, delivery ACK
36.743 ms, cancellation did not insert a second result, no microphone, and no
voice-extension disposal stack in Shell logs. General GNOME/portal shutdown
warnings remain separate from extension behavior. Unified local CPU acceptance
is now rerunning against current source/tests and the latest extracted worker;
the final requirement-by-requirement handoff audit follows that run. Remote CI
and physical Lunar Lake/microphone results must remain explicitly unverified.

Real desktop end-to-end smoke PASSED on local GNOME Shell 50.1, using extracted
packages and a separate headless Wayland compositor/bus/virtual monitor. Test
staging /tmp/anduinos-shell-e2e.gdb3ksni. Verified: real Shell extension loads;
ordinary caller Start is denied; real native CPU output from the pinned public
fixture reaches a focused GTK TextView (11 expected words); Stop retains the
in-flight final result; delivery ACK appears (36.938 ms dispatch/ACK, not total
speech latency); Dismiss cancels another in-flight request without adding text,
and the service exits cleanly. Physical microphone capture is replaced by a
public-fixture callback; its endpoint metric is synthetic, not measured VAD.

The first desktop attempt exposed a real extension shutdown error: accessing
chrome after GNOME had disposed its actors. Removed the unnecessary hide() from
the shutdown callback, retaining daemon shutdown; Node regression covers already
disposed actors. GTK package rebuilt successfully (30 tests, display opt-in
separately tested). Rerun no longer shows voice-extension disposal stacks; GNOME
itself still logs ibusCandidatePopup disposal warnings during headless shutdown.
The initial harness also imported Gtk before Wayland existed; deferring import
fixed its initialization failure. Neither was waved away as a passing run.

New GTK scripts/smoke-shell.py and isolated schema overrides document/reproduce
this path. XDG isolation is applied before starting D-Bus so activated services
inherit it, with memory settings, no replacement of the current Shell, and
owned-child cleanup. Three guard tests prevent accidental use of the normal
runtime/session environment. A final rerun with strengthened process-group
cleanup is in progress. Remote CI execution remains unverified; local CPU
entry-point and separate hardware/desktop tests are distinct evidence.

Selected-policy full-corpus acceptance PASSED with the rebuilt packaged worker:
16 auto/en/zh-Hans short/long clean/noisy cases, no increased error counts versus
matching-language CPU/four-thread CLI. Real measured choices: auto CPU/eight
(35.417 s calibration), English GPU/four (12.300 s), Chinese CPU/eight (14.379 s).
Report: voice-test-results/selected-corpus.json. All 112 framework tests also
passed with native opt-ins (11.376 s), including the new missing-library test.
Both native architectures and framework rebuilt successfully before this run.

GPU stability additionally PASSED on the development workstation: 100 requests,
three cancellation/recovery cycles, elapsed 10.16 s, warm median 92.01 ms and
P95 138.43 ms. Actual backend was GPU for every continuous-phase request. Host
RSS growth 0.17/0.20 MiB, 29 descriptors/five threads per worker, all children
reaped. These measurements do not measure VRAM growth, certify Lunar Lake, or
override the known raw GPU noisy-Chinese accuracy mismatch. Automatic selection
continues to reject that mismatch. Report: voice-test-results/gpu-stress.json.

Package review found libggml0 only Suggests GPU plugins. Worker now Recommends
libggml0-backend-vulkan >=0.9.11 so a normal APT installation includes the optional
plugin; CPU-only --no-install-recommends remains supported. No proprietary driver
is installed/replaced by this recommendation. New dependency-policy unit test
passed and internal package graph still passes. Rebuilding worker metadata now;
final end-to-end desktop review and remote container/CI verification remain.
The recommendation rebuild subsequently passed for both architectures;
dpkg-deb confirms Recommends in both outputs. Latest amd64 executable/private
library cmp-identical to the payload used for selected-policy/GPU tests.
ARM64 Vulkan package is available from Ubuntu Ports; mirror HEAD initially
returned 404 but a ranged GET returned 200. No driver or plugin was installed
on this workstation. Added recommendation test passes separately (full suite
was 112 before this additional test).

Extended packaged-worker CPU stability passed: 300 uninterrupted requests plus
three cancellation/recovery cycles, elapsed 330.20 s. Post-warm-up peak RSS
growth was 0.25 MiB English and 0.24 MiB Chinese; descriptors remained 26 and
threads four. Warm wall-time median 1078.99 ms, P95 1296.92 ms. Source benchmark
editing/lightweight tests occurred while it ran; no other inference or build
load was started. This extends the 100-request evidence without changing the
64 MiB budget or restarting to conceal growth.

Static review also found missing private libraries/symbols exited silently,
misclassified by Python as generic recognition failures (and possibly retried on
CPU). Native startup now reports a bounded unavailable status, with no dlerror
or private paths, mapped to ResidentUnavailable and a reinstall message. New
native missing-library regression test fails against the preceding package as
expected. Rebuilding both native architectures and framework before verifying
the fixed path and running selected-policy corpus. Normal inference unchanged.

Both amd64 and arm64 worker packages with malloc_trim now build successfully.
New amd64 package extracted to /tmp/anduinos-trim-package.OZIaHal2; readelf
confirms its malloc_trim reference. A 300-request uninterrupted stress run is
active against this frozen package, not mutable build outputs (reports:
voice-test-results/cpu-stress-300.json and cpu-stress-300-progress.log). It has
crossed request 80 with less than 0.2 MiB growth per worker; not yet complete.

Added scripts/benchmark-selected.py to validate selected backend/thread settings
on the full relevant corpus, using matching auto/en/zh-Hans language modes and
a fixed CPU/four-thread CLI accuracy reference. Temporary cache, same model,
no exported text. Non-measured fallback, incorrect actual backend, empty output
or increased error counts fails the gate. Four unit tests passed; real selected
policy validation is pending until the long stress run finishes, to avoid CPU
contention. This script is not yet claimed as a passing acceptance gate.

The full updated CPU entry point passed with the malloc_trim candidate (staging
/tmp/anduinos-voice-ci.A8cAqmOT): framework 107 tests with native opt-ins, GTK 30
with real private-display widgets (no skips), Node/controller and private D-Bus,
all eight CPU corpus accuracy comparisons, 100 continuous requests and three
cancellation/recovery cycles. The unchanged 64 MiB growth guard passed with
0.18 MiB post-warm-up growth for each language, 26 descriptors/four threads each.
Stress elapsed 113.07 s, warm median 1069.42 ms, P95 1297.70 ms. Public corpus
state-release timings including trimming were 2.518–4.432 ms. These observations
support allocator retention as the cause of the earlier RSS growth; they do not
prove indefinite leak freedom or a universal latency improvement. No gate was
relaxed. Reports are in voice-test-results (ignored, regenerated per run).
Both-architecture worker package rebuild is in progress; next validate a longer
continuous run from the extracted new amd64 package and selected-policy corpus.

RSS failure reproduced independently at request 78: Chinese worker baseline
288.53125 MiB, current 357.671875 MiB, descriptors 26 and threads four unchanged.
The earlier CI entry-point failure is therefore not dismissed as a one-off.
Added malloc_trim(0) immediately after per-request state release as a candidate
fix for freed allocator pages retained by the resident process; release timing
includes this cost. This releases unused heap memory, not live model weights
(https://man7.org/linux/man-pages/man3/malloc_trim.3.html). Allocator retention is
still a hypothesis, not a proven diagnosis of every allocation. Latest amd64
worker compiles; a full CPU acceptance run using this new binary is in progress.
Do not claim the RSS or latency regression is fixed until that run and further
repeat/corpus checks prove it. Packaged worker debs still predate this change.

The latest CI entry point also requires real GTK tests under private D-Bus/Xvfb,
and compiles translations. The isolated full GTK suite passed all 30 tests after
supplying PYTHONPATH correctly (an initial manual invocation omitted it and
failed two diagnostic imports; the CI script already exports it). Added explicit
cross libc/std C++ runtime packages to container provisioning. CI YAML validation
uses a SafeLoader extension for GitLab's existing !reference tag, not bare YAML.

New mandatory voice-cpu-acceptance CI job and lib/test-voice-cpu.sh entry point
cover native opt-in tests, GTK/controller tests, private D-Bus export, all eight
CPU corpus cases, and 100-request CPU stress. CI builds both native architectures
and downloads/verifies Base; local callers may explicitly test extracted worker
and model inputs. Worker publication now requires this job, transitively gating
framework/GTK publication. The package-needs verifier explicitly requires this
non-package edge; four new policy tests verify removal/renaming/extra edges fail.
CI YAML parses and existing 81-project/104-relationship policy passes. Reports
are artifacts retained on failure, excluded from source control. TESTING.md
documents invocation, environments and hardware limits.

IMPORTANT: first local entry-point run FAILED the RSS stability gate after 80
requests (before request 100). Framework 107 tests, GTK 30 (display opt-in skipped
in that first run), controller/D-Bus and CPU corpus accuracy passed beforehand.
Do not treat earlier successful stress runs as proof this failure is harmless.
Added numeric baseline/current RSS, thread and descriptor progress/failure
details without speech. A separate 100-request reproduction is running; the
64 MiB gate remains unchanged. Latest script additionally makes isolated Xvfb
GTK checks mandatory and saves stress stderr as a CI failure artifact; that
latest complete entry-point version has not yet passed end-to-end.

CI image/provisioning is not validated remotely: Docker socket access on this
workstation is denied, and anonymous GitLab runner/job API returned HTTP 401.
No permission changes, authentication bypass, install or push was performed.
The job requires an amd64 Docker executor and refuses package installation on
a persistent shell executor. Its container dependency list and cross-toolchain
setup still need execution in that environment before CI readiness is claimed.

Calibration diagnostics wiring is now fixed. AutomaticSelector exposes only
this select call's bounded, sanitized measurements, including completed probes
before cancellation/failure. Manual/cached/pre-cancelled calls clear these
observations instead of replaying stale benchmark data. The daemon transfers
them to its allow-listed history in a finally block, with the session model.
Tests cover privacy, 100-record bound, no cached/manual replay, error/cancel
preservation, and preparation-worker-to-export history wiring.

Validation after this fix: all 107 framework tests passed with native opt-ins
enabled (11.893 s). Clean-staging apkg build --all succeeded and the updated
framework deb was extracted into the combined payload. The real private-D-Bus
service/CLI smoke test passed against these packaged imports, now checking
both synthetic final and cold benchmark records: numeric/enumerated fields
survive, transcript/path/log fields do not, absent service is not auto-started,
microphone access is prohibited, and service threads shut down cleanly. The
transport smoke uses synthetic metadata; native inference is separately covered.

Preparation UI now distinguishes actual calibration from model warm-up. The
selector calls on_measure only for a real measurement (not manual/cached use);
the daemon schedules a session-guarded calibrating state on the main loop, then
returns to preparing before warming the selected engine. Delayed callbacks are
ignored after cancellation, session replacement or microphone capture start.
Shell treats calibration as cancellable active preparation, shows a localized
"Measuring performance — microphone off" message without recording styling,
and forwards the translated status detail to settings. Performance settings
explain roughly minute-long first-use/retest, caching, when to speak and how to
cancel. Added English message catalog entries and Chinese translations.

Verification: framework 105 tests passed including all native integration
opt-ins (10.572 s); Node controller regression passed calibration/translation/
cancel checks alongside finishing/order/password protections. GTK apkg build
passed (30 tests, display opt-in skipped in build), and isolated Xvfb/private
D-Bus/memory-settings widget tests separately passed all four tests. Portal
warnings from the incomplete private desktop and existing GTK deprecations did
not fail tests. This is not an actual GNOME Shell rendering/end-to-end proof.
Framework rebuild also passed; both latest debs were extracted into the combined
payload and packaged daemon.py matches current source. Final native CI gating, selected-policy corpus
acceptance and end-to-end review still remain. Also verify calibration metrics
reach diagnostic export: current daemon inspection shows only warm-up/inference
records copied to history, not the tuner's per-candidate measurements.

Selection policy v5 reduces first-use exploration cost without qualifying any
partially checked candidate. Probe order is CPU/four threads, GPU/four, then
CPU/eight and CPU/two (deduplicated/clamped to affinity). Stop a candidate on
wrong actual backend, empty/unstable/different output, or after two clean warm
runs are over 1.5 times slower than a fully quality-validated candidate's clean
warm median. This last rule is a bounded exploration heuristic, not a proof of
the fastest configuration for every utterance. Winners still complete all nine
auto-language requests and match every clean/noisy bilingual baseline output.
Failed GPU candidates cannot supply the speed-pruning bound.

On the same workstation/Base/packaged worker, real auto-language calibration
fell from 55.82 s (v4) to 35.33 s (v5), selecting CPU/eight threads again. Actual
requests: CPU/four 9, GPU/four 8, CPU/eight 9, CPU/two 3. The output-mismatching
GPU and slow CPU candidate exit early; selected CPU/eight completes all checks.
This is a single before/after measurement, not a multi-device speed guarantee.
Tuning tests 27 passed; full framework tests 103 passed, first without native
opt-ins and subsequently with all six native tests enabled (10.633 s, no skips).
The latest framework apkg build --all succeeded in clean staging. Extracted the
new deb into the combined payload and cmp verified its tuning.py exactly matches
current source. First-use setup still needs clear UI feedback; cached use does
not repeat this calibration. CI/native acceptance wiring and final end-to-end
review remain outstanding; this is not a release-completion claim.

Latest CPU stability gate: scripts/stress-resident.py passed 100 continuous
requests against the extracted final amd64 worker in
/tmp/anduinos-upper-packages.WGYigm/payload, using Base, four threads and all
eight clean/noisy bilingual fixtures. Each language retained its worker PID
through the continuous phase. Three subsequent cancellation/recovery cycles
passed and all child processes were reaped. Total 113.89 s; warm wall-time median
1090.23 ms, P95 1270.42 ms. Post-warm-up peak RSS growth was 10.78 MiB (English)
and 9.46 MiB (Chinese), under the explicit 64 MiB gate; each worker stayed at
26 descriptors and four threads. These are i9-13900KS workstation measurements,
not Lunar Lake results and not proof of indefinite leak freedom. Five unit tests
check stress-gate failures, including unstable output and resource growth.

Selection policy v4 now checks both English and Chinese short fixtures in actual
auto-language mode, including noisy variants, instead of benchmarking only
forced English for auto users. Per-condition repeated output must match the CPU
baseline before speed can select a candidate. Explicit thread counts are measured
directly, not substituted after benchmarking another configuration. Language
mode and effective manual threads now participate in cache invalidation, along
with both sample hashes. Tuning unit tests: 26 passed, including a fast GPU with
a regression only on the second language and settings/cache separation.
Real bilingual automatic-selection validation completed using the packaged
amd64 worker and current selector source: all 36 requests completed in 55.82 s,
actual GPU execution was observed, and the measured choice was CPU/eight threads.
This does not establish all-model/all-language or GPU accuracy acceptance.
The nearly minute-long first-use calibration on this fast workstation is also
a remaining UX problem: retain quality checks while reducing preparation cost
and preventing slow candidates from consuming the entire exploration budget.
Full framework regression after the v4 selector and stress additions: 102 tests
passed in 10.729 s from clean staging, with native worker/sample opt-ins enabled
(no skips). Includes actual CPU process reuse, cancellation, crash restart,
timeout reaping and GPU backend reporting. Framework deb still predates these
latest Python changes and must be rebuilt before release acceptance.

Upper-package acceptance: framework 2.0.2-12 and GTK 2.0.2-21 built successfully
with apkg build --all from clean staging /tmp/anduinos-upper-packages.WGYigm.
Framework build ran 93 tests (6 native opt-ins skipped), GTK 30 (display opt-in
skipped). GTK initially failed because staging omitted anduinos-desktop and
anduinos-desktop-core manifests required by its dependency test; copied those
unchanged manifests and reran successfully, without weakening/skipping tests.
Unpacked all three amd64-compatible packages together into staging/payload and
verified executable modes, dependencies, absence of pyc and the CLI benchmark
adapter, four fixture hashes, private-library license, strict GSettings schemas.

New scripts/smoke-diagnostics.py exercises packaged service and CLI on a private
D-Bus session with memory GSettings: absent service does not auto-start; running
service exports only synthetic allow-listed fields; microphone access is guarded
against; shutdown joins recognition/service threads; export again fails without
restarting the service. This real D-Bus check passed against package imports.
Packaged GTK performance group was mapped/allocated in Xvfb with private bus and
memory settings; backend/thread bindings, reset preserving model/microphone, and
compiled Chinese translation passed. Portal/Wayland warnings occurred because
this is not a full compositor session; do not present it as GNOME Shell end-to-end
validation. No package was installed or pushed. Remaining primary acceptance:
long-run/resource behavior, complete selected-policy GPU/corpus validation,
first-use/cancellation/text-delivery end-to-end checks, and enforceable CI gates.

Production build integration completed for the worker: pinned whisper.cpp plus
the state metrics extension is a private libanduinos-whisper.so.1, loaded by a
fixed sibling path relative to /proc/self/exe. Required metric symbol checked.
No libwhisper1 runtime dependency/search remains; dependencies are distro GGML,
libstdc++, libgcc and libc. MIT notice and updated provenance ship in the deb.
Build locally extracts SHA-pinned C++ frontends/headers and target GGML link
libraries; host requirements are amd64 with native/cross GCC 15 drivers. It never
installs build packages or executes arm64 binaries. Intermediate outputs are
separated into obj/amd64 and obj/arm64. Apkg does NOT substitute $(Arch) in source
paths: explicit conditional IncludeFile/IncludeScript entries are used instead.
Both target debs successfully rebuilt with apkg build --all after that correction.

Independent request state is now the ONLY production runtime path; removed the
fresh-state switch and old state-reuse/timing-log branches. Full six-phase metrics
and lifecycle counters are always available. Tuning policy v3 fingerprints the
private library, with a dedicated invalidation test (22 tuning tests passed).
Validation used the extracted final amd64 package, not a mutable build output:
92 framework tests passed including native integration. The unchanged CPU corpus
accuracy gate passed on all Chinese/English short/long clean/noisy cases. An
earlier test run overlapped cross compilation and failed three starts when shared
intermediates changed architecture; this was corrected by architecture isolation
and rerunning all nine native tests from a frozen extracted package (all passed).
Arm64 executable and library ELF architectures verified; arm64 runtime remains
untested. Private library SONAME and dependencies verified, no build-directory
RPATH/RUNPATH. Extracted final amd64 package and clean framework test staging:
/tmp/anduinos-release-validation.8dxK0Q. Prior obj/anduinos-whisper-worker is stale;
use the architecture-specific outputs or an extracted package going forward.
Remaining main work: framework/GTK package builds, selected-policy GPU/full-corpus
and long-run acceptance, end-to-end service/UI checks, and CI enforcement.

State telemetry prototype now works. `../anduinos-whisper-worker/src/state-metrics.cpp`
compiles alongside SHA-256 pinned upstream 1.8.3 source and adds one read-only C
function returning six accumulated millisecond counters. It neither modifies
decoding nor guesses opaque layouts across a binary boundary. The worker probes
this symbol and exports measured fresh-state phases when present; absent phases
remain omitted with the stock library. `build-state-metrics.sh` reproduces the
native-host development build under obj/state-metrics (not yet a package build).
Source, architecture header and MIT license downloads are hash checked.
After /tmp was cleared between turns, compiler packages were downloaded/extracted
under obj/state-metrics-toolchain, without system installation. The C++ frontend
is under usr/libexec/gcc/x86_64-linux-gnu/15, not usr/lib/gcc. Build tested using
gcc -x c++ with that -B path and the extracted standard-library include paths.
Nine resident tests passed with the prototype library (LD_LIBRARY_PATH scoped to
the command), including fresh-state positive phase timings and stage-sum sanity.
Five real clean zh-long requests all produced stable text containing 读者. Example
CPU/4 metrics: total inference 1237 ms, encode 759 ms, batch decode 391 ms, decode
3 ms, mel 11 ms, sample 59 ms; state init 8 ms/release 3 ms. These are workstation
observations, not Lunar Lake results. No system library was replaced. Remaining:
integrate a private reproducible library into both architecture packages, retain
source/license provenance, adopt/validate the single state-isolated runtime,
then rerun complete corpus, lifecycle, packaging and end-to-end acceptance.

First-release simplification: removed SessionEngine's CLI compatibility path and
native-unavailable memoization. Missing/incompatible worker is an explicit error;
GPU inference failure retries the same model once on resident CPU. If that retry
fails, both failed engines are closed and there is no third attempt. Developer
CLI reference moved from installed src/engine.py to scripts/benchmark_engine.py;
runtime errors now live in errors.py. Removed whisper.cpp CLI runtime dependency
(worker retains its libwhisper dependency). Updated all benchmark/test imports and
package dependency checks. No installed runtime module imports the CLI adapter.
Framework fresh staging: 92 tests passed including native integration. GTK: 30
run, isolated-display test skipped, remaining passed. diff check passed. New
staging: /tmp/anduinos-first-release-validation.1WuotO. Earlier ledger references
to CLI compatibility fallback describe superseded work, not the current design.

Fresh-state lifecycle refinement: use public no-state model initialization rather
than retaining an unused default state alongside each request state. Export
state_initialization_ms and state_release_ms separately; inference_ms now excludes
those lifecycle costs (supersedes the earlier experimental timing definition).
Startup backend is unknown until request state is initialized, and actual GPU
activation is re-evaluated for each new state. Model/process remain resident.
New native test verifies same PID/text across requests, nonzero lifecycle metrics
and absence of fabricated encoder/decoder metrics. Full framework 91 tests passed
with native integration; after the backend-reporting refinement, three real CPU
zh-long requests were correct. Observed state init 3–14 ms, release 2–5 ms,
inference 1.25–1.54 s, peak RSS 382–401 MiB (workstation only, not a hardware-wide
promise). Public APIs still expose detailed stage timings only for default state;
independent-state encoder/decoder metrics remain a genuine unfinished requirement.
No opaque-struct layout tricks or new inference framework were introduced.

Automatic tuning policy v2 now evaluates five requests per candidate: cold clean,
two warm clean, two warm deterministic 20 dB SNR noisy. It requires nonempty,
stable output for each condition and compares the pair against a CPU baseline;
every request must report the requested backend. A deadline is checked before
each request, not only between candidates. Noise generation is shared by the
runtime tuner and offline corpus benchmark. Policy version bump invalidates old
clean-only caches. Three new tests cover noisy-only regression, noisy instability,
and deadline exhaustion (21 tuner tests passed). Full framework unit run: 90
tests, five native integrations skipped, remainder passed.
Real Base/zh-Hans probe using the experimental fresh-state engine evaluated all
20 requests and selected CPU/8 threads; the faster GPU's noisy output failed
consistency with CPU. This verifies selection behavior, NOT the unmodified
default engine or Lunar Lake performance. Fresh-state production integration,
phase telemetry, full corpus and packaging acceptance remain unfinished.

GPU parity diagnosis now has a reproducible `--compare-cli-gpu` corpus option.
CLI diagnostics identify GPU only from the engine's actual backend activation
message, never device enumeration. New tests cover that boundary and ensure the
additional CLI-GPU comparison cannot replace the CLI-CPU accuracy baseline.
The real 32-row fresh-state comparison found identical error counts between
resident and CLI for each corresponding backend: all English zero; Chinese
clean short/long zero; noisy short CPU zero/GPU one; noisy long all three.
Thus the remaining noisy-short GPU discrepancy also occurs in upstream CLI on
this workstation, not solely our transport/state wrapper. This does not prove
identical transcripts or the underlying numerical cause. Overall gate remains
failed against CPU; no default changes. Future candidate selection should include
noisy quality checks rather than accepting GPU on clean-sample speed alone.
Focused corpus/diagnostic tests: 3 + 9 passed.

State-isolation experiment: eight fresh native CPU processes and eight fresh CLI
runs transcribed clean zh-long correctly, while one reused native process again
produced 独者 instead of 读者 on its fourth request. New opt-in `--fresh-state`
worker/benchmark switch keeps model weights loaded but creates/frees request state
through public APIs. Eight repeated requests then all matched (1.26–1.34 s on
this workstation). Full 24-case CLI/CPU/GPU corpus comparison: experimental CPU
matched CLI error counts on every case; GPU clean zh-long now matched too, but
GPU noisy zh-short still had one error versus CLI zero. Overall gate still FAILS.
This separates a state-reuse-associated effect from a remaining GPU discrepancy;
the underlying library defect is not yet proven. The application default has NOT
changed. Public timing APIs cannot inspect the separate state, so experimental
phase metrics are omitted, never fabricated as zero. State setup is included in
experimental inference timing. The original default path's 85 framework tests,
including native integration, passed after this change. The corpus and suite were
launched separately with possible overlap; corpus latency is not a controlled
performance acceptance result. Future work: GPU parity investigation, state
lifecycle cost/telemetry, then unchanged accuracy and performance acceptance.

Performance UI now offers auto/CPU/GPU-with-CPU-fallback, 0..256 threads, retest
on next start, and restore automatic defaults. Retest switches backend to auto and
increments the persisted generation; reset additionally resets only threads.
Neither action starts the service or changes model/microphone/language. Chinese
translations added. GTK suite: 30 passed in Xvfb with memory GSettings and staged
schema; four dedicated tests include real widget setting bindings, reset scope,
generation rollover, and an actually mapped/allocated performance page. msgfmt
and diff whitespace checks passed. Existing PreferencesWindow APIs emit upstream
deprecation warnings; no API migration is claimed by this change.

Startup integration now snapshots model/language/backend/threads per session,
selects a configuration on the recognition thread, warms that same persistent
engine with a checked public fixture, and only then starts microphone capture.
The Shell recognizes a translated preparing state. Finish/Stop during preparation
cancels work; stale completion callbacks cannot reopen the microphone. Fixture
text never enters Transcript/desktop delivery. Three new regression tests cover
these paths; the framework clean-staging suite ran 85 tests including all five
native integration tests successfully. GTK 26 tests, msgfmt, node syntax and
diff whitespace checks passed. This does NOT resolve the Chinese accuracy gate.

Latest incremental validation: persisted `tuning-generation` now invalidates
both disk selections and the in-process failed-probe cache, with restart coverage.
Backend/thread/generation settings are declared with schema constraints but are
NOT yet wired into the daemon/UI. Failed-probe reuse now preserves manual thread
overrides, and pre-cancelled selections cannot take the cache/manual fast paths.
Tuning tests: 18 passed. Full framework clean-staging suite: 82 run, 5 native
integration tests skipped (no native test environment supplied in this run).
GTK: 26 passed. Strict schema dry-run and diff whitespace checks passed.
The source-tree framework run found four pre-existing Python cache files; these
were preserved and the payload check passed in staging excluding cache files.
No new native accuracy/performance claim follows from this unit-test run.

1. Validate that native decoding defaults match existing behavior before any
   tuning, especially resolve the failing Chinese corpus gate above; complete
   the framework build with its new worker dependency and
   strengthen native runtime CI gates (native integration currently opt-in).
2. Rendered settings export/UI smoke checks and a private end-to-end D-Bus
   diagnostic check; review failure/cancellation measurements for completeness.
3. Complete end-to-end preparation failure/cancellation and rendered Shell checks;
   manual overrides and restore/retest UI are now present.
4. Accuracy-gated thread/decoding/backend experiments; long-run/resource tests,
   both architecture builds and a noninteractive end-to-end transport check.
5. Real Lunar Lake measurements remain unavailable; the workstation measurements
   must not be represented as evidence of that device's performance.

## Local working evidence paths (temporary, not release artifacts)

- Clean test staging: `/tmp/anduinos-performance-validation.wUNXTe` (avoids
  pre-existing ignored Python cache files in the source checkout).
- Extracted packaged amd64 worker:
  `/tmp/anduinos-resident-build.yritg0/packaged-amd64/usr/libexec/anduinos-whisper-worker`.
- Public integration sample: `/tmp/anduinos-voice-research.JCHOBz/jfk.wav`.
- Worker packages: `../anduinos-whisper-worker/bin/`.
- Original licensed FLEURS archives/TSVs: `/tmp/anduinos-voice-corpus.NFEEiN`.
- Latest experimental worker (amd64, supports optional no-fallback):
  `../anduinos-whisper-worker/obj/anduinos-whisper-worker`.

## Native integration test entry point

Set `ANDUINOS_VOICE_WORKER` to the compiled helper and `ANDUINOS_VOICE_SAMPLE`
to a consented/public mono 16 kHz PCM16 WAV, then run the framework unittest
suite with `PYTHONDONTWRITEBYTECODE=1`. Do not point it at private recordings for
tests that print public-sample transcripts. The default model path is the
installed Base model; packaging tests will supply their own staged model.
