# Voice typing tests

Run commands below from the repository root. Tests use licensed public fixtures
and synthetic noise, never a microphone or private dictation.

## CPU acceptance

```sh
bash anduinos-whisper-framework/tests/run-cpu.sh
```

This builds the worker for amd64 and arm64, verifies the pinned Base and Silero
models, and runs Python/GTK tests, Node controller tests, private-D-Bus checks,
eight bilingual clean/noisy CLI comparisons, 100 repeated CPU requests, 32
capture cases, 18 non-speech noise cases, and two 30000-frame VAD replays.
ARM64 is compiled, not executed. This optional manual suite requires an amd64
host with GCC 15 and its arm64 cross compiler, libc development files, Python 3
with GI, GTK 4/Libadwaita and GStreamer introspection, GStreamer base/good/bad
plugins, OpenCC, GGML, whisper-cli, Node.js, gettext, D-Bus, Xvfb, xauth and curl.
It checks prerequisites but never installs host packages.

To reuse an extracted package, preserving the worker's private library layout:

```sh
ANDUINOS_VOICE_WORKER=/absolute/payload/usr/libexec/anduinos-whisper-worker \
ANDUINOS_VOICE_MODEL=/absolute/payload/usr/share/anduinos-whisper-framework/models/ggml-base.bin \
ANDUINOS_VAD_MODEL=/absolute/payload/usr/share/anduinos-whisper-framework/models/ggml-silero-v6.2.0.bin \
bash anduinos-whisper-framework/tests/run-cpu.sh
```

These overrides skip building/downloading those inputs, not runtime tests.
Source is copied to clean temporary staging without modifying developers'
ignored caches. Reports go to `anduinos-whisper-framework/obj/voice-test-results/`;
temporary staging is removed on exit. These reports are not tracked by Git.

Package tests run through each package's `PrebuildCommand`. This longer suite
is a manual diagnostic tool, not a separate CI job or publication prerequisite.
It does not certify remote runner provisioning.

## Focused benchmarks

Tools are in `anduinos-whisper-framework/tests/benchmarks/`. Run each with
`--help` for model, worker and backend options.

| Tool | Contract |
| --- | --- |
| `benchmark-corpus.py` | Resident CPU errors must not exceed matching CPU CLI errors. `--gpu` additionally requires actual GPU inference. |
| `benchmark-selected.py` | Test measured automatic selection across auto/English/Chinese, comparing actual backend and accuracy with CPU CLI. |
| `benchmark-capture.py` | Real DSP, native streaming VAD and phrase completion. Use `--normalize-dbfs -26` for the CI input levels. |
| `benchmark-noise.py` | Hum, fan-like and tapping noise at three levels, DSP off/on; reject false triggers. |
| `stress-resident.py` | Stable repeated output/processes, bounded RSS growth, descriptor/thread counts, cancellation and cleanup. |
| `stress-vad.py` | Deterministic replay, resource bounds and cleanup across two simulated 16-minute streams. |

`benchmark_engine.py` is the CLI reference adapter shared by these tests,
not a production inference backend. Do not run timing benchmarks concurrently
with builds or other heavy benchmarks.

### Interpretation and limits

- Raw ASR accuracy is a strict gate. Capture's exit status gates phrase
  completion only; inspect its separate `accuracy_nonregression` and error
  counts. Some Chinese cases regress after frontend processing; passing
  endpoint tests is not proof of accuracy nonregression.
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

```sh
python3 anduinos-whisper-gtk/tests/integration/smoke-shell.py --payload /absolute/extracted-packages
```

Requires GNOME Shell with headless Wayland support and usable rendering.
The payload must contain worker, framework/models and GTK packages. The test
uses a private bus, virtual monitor, temporary settings/cache/runtime and public
audio in place of capture. It never replaces the current Shell or opens a mic.
Recognition, authorization, clipboard/keyboard dispatch and GTK reception are
real. Finish must retain the final result; Dismiss must prevent late insertion.
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
