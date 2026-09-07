# Voice typing implementation audit

Current local audit: 2026-09-07. This is not a claim of laptop validation or a
published release. The historical development ledger is PERFORMANCE-WORK.md;
commands and interpretation are in TESTING.md.

## Requirement evidence

| Requirement | Implementation and evidence | Status / boundary |
| --- | --- | --- |
| One ASR engine, CPU fallback, optional GPU, no per-laptop hacks or required NPU | Private pinned whisper.cpp 1.8.3; worker.c, session_engine.py, tuning.py. Native CPU, actual GPU and bounded fallback tests. | Implemented; actual hardware is i9-13900KS/RTX4090, not Lunar Lake. |
| Persistent isolated model, reload, idle release, cancel/crash/timeout recovery | WorkerTransport and SessionEngine; same-PID native tests, changed model/config tests, 180-second idle lifecycle, abort/kill/reap tests. Healthy warm preparation no longer decodes a fixture again. | Verified locally. No production per-phrase CLI fallback. |
| Local measured backend/thread selection, same model quality | CPU 2/4/8 candidates bounded by affinity, actual GPU candidate, same beam5/default decoding, public clean/noisy fixtures and cold/warm separation. selected-final.json: 16 cases, all measured, no added errors versus matching CPU CLI. | Auto-language CPU8; English GPU4; Chinese CPU8 on this workstation only. |
| Cache invalidation and user controls | Private bounded cache; fingerprint includes model, worker, private library, GGML core/plugins, graphics libraries/ICDs and environment. Tests cover library/model changes, retest, failure memo reset, manual controls and preserving model/mic settings. | Implemented. GGML-core-only update regression test added during audit. |
| No audio/text in diagnostic reports | Allow-listed PerformanceHistory and client revalidation; endpoint/queue/init/encode/decode/delivery fields, separate VAD startup engine label. Export and no-auto-start tests. | Verified metadata boundary. Endpoint means last VAD-positive audio frame, not exact human acoustic endpoint. |
| Actual desktop insertion and cancellation | Isolated GNOME Shell50.1, packaged service/extension, real native ASR, clipboard/keyboard and GTK TextView. Latest smoke jyjlfp6n: 11 public words, delivery36.966ms, Finish retained final, Dismiss inserted nothing. | Passed; physical capture replaced with public fixture. Delivery ACK is not proof for every desktop app. |
| Noisy/quiet input and endpoint behavior | Actual optional WebRTC DSP + isolated one-thread Silero stream. capture-silero-normalized.json: all32 cases finish; frontend errors31 versus raw30, former frontend83 on same matrix. | Substantial improvement, NOT blanket accuracy nonregression. Individual Chinese-long cases still differ. |
| Non-speech false triggers | noise-shapes.json: hum/fan-like/tapping, three levels, DSP off/on, 18 cases, zero completed or active phrases. | Synthetic shapes only, not a real-world noise certification. Background speech is not covered by these non-speech labels. |
| Streaming detector correctness and stability | 12996 private-pipe frames match upstream batch probabilities; vad-stress.json: 2x30000 frames, deterministic replay, RSS growth0MiB, one thread, both children reaped. | Faster-than-real-time simulated audio, not a wall-clock soak. |
| Fixed bilingual short/long clean/noisy accuracy and CPU-only gate | Licensed pinned four-clip corpus plus reproducible noise; strict raw CPU versus CLI gate and repeated output stress. Latest completed entry point:139 framework tests,33 GTK tests plus native/Node/D-Bus/corpus/stress. | Expanded entry point passed in staging Gr1OJBEd, including32 capture cases,18 shaped-noise cases and2x30000 VAD frames. Frontend accuracy remains separately reported, not passed. |
| Separate real GPU evidence | selected-final.json includes actual English GPU inference; gpu-stress-final.json:100 requests,3 cancellations, same output/PIDs, host RSS growth0.22/0.20MiB. | Host RSS, not VRAM. Raw GPU accuracy has a known noisy-Chinese difference; automatic selection must reject output-changing candidates. |
| Packaging/build | apkg build --all succeeded for worker amd64+arm64, framework all and GTK all. Extracted runtime Python matches current source. VAD model and MIT notices bundled, build-time hash verified. | ARM64 compiled, not executed. No host installation or remote publication performed. |
| Diagnose the user's Lunar Lake delay | Timing export and the necessary code paths exist; no new laptop report, microphone capture or hardware session was supplied. | **Not established.** Workstation measurements cannot prove this requirement. |

## Packages under test

Combined extracted payload: `/tmp/anduinos-vad-packages.dfX5BsOq/payload`.
Framework/GTK debs are under the corresponding package directories in that
staging tree; worker debs are in `../anduinos-whisper-worker/bin`.

| Deb | SHA-256 |
| --- | --- |
| worker2.0.2-1, amd64 | `410f6aa8864b072c347ae16b0b71daea1e2f33d154c674e5e692f920c3c047d5` |
| worker2.0.2-1, arm64 | `52c59186b5a5366c3d934495cb7ef6726e90d613d53ab1f7dcbf37a87540ea76` |
| framework2.0.2-12, all | `01edcad9edc622640dd9f5dea408430e2b0fd4c22487e412cad6e8e4b8ee3f0d` |
| GTK2.0.2-21, all | `89cdf5a682913bc6a9496266cd6ee73191719a2778df8e2a61717289f7d4592f` |

All are resolute-addon builds. Rebuilding can change the deb hash; refresh this
table rather than treating a previous package's results as evidence for another.
JSON reports are in the repository's ignored voice-test-results directory.

## Final local rerun and remaining validation

1. Expanded unified entry point completed with exit0 against the final extracted
   worker, Base and VAD models. CPU100-request stress: warm median1104.65ms,
   P951318.39ms,3 cancellations, RSS growth0.18/0.21MiB. Capture32/32 endpoint
   cases passed, errors31 versus raw30; noise18/18 passed. VAD2x30000 frames
   passed with zero measured RSS growth and both children reaped. These are
   workstation/public-fixture results, not laptop or physical microphone results.
2. Final raw GPU comparison inspected: noisy Chinese-short has one character
   error versus zero for CPU CLI/resident CPU; the other seven cases have matching
   error counts. Its nonregression gate correctly exits nonzero. This is distinct
   from the passing automatic-policy result, which selected CPU for Chinese.
3. Obtain a diagnostic report from the actual Lunar Lake laptop when the user can
   test. Required context: model/language/backend, live-preview setting, whether
   the session was cold or warm, and whether the delay preceded or followed the
   end of speech. No recording/transcript is required for the timing diagnosis.
   Follow the reproducible laptop timing handoff in TESTING.md.
4. Remote GitLab/container provisioning and physical microphone behavior remain
   unverified. Neither a push nor a host installation is authorized by this audit.
