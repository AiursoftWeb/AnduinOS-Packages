# AnduinOS voice typing engine

Offline dictation with PipeWire/GStreamer capture, streaming Silero speech
detection, and an isolated persistent whisper.cpp worker. The desktop UI and
text insertion live in the sibling `anduinos-whisper-gtk` package; native
inference lives in `anduinos-whisper-worker`.

## Layout

- `src/`: installed service, capture, scheduling, inference and diagnostics.
- `data/`: service/schema definitions and licensed public calibration audio.
- `scripts/`: build-time model downloads with pinned checksums.
- `tests/`: unit tests; `benchmarks/` contains reproducible accuracy/performance
  checks and `integration/` contains isolated service checks.
- `docs/testing.md`: acceptance commands, report interpretation and laptop checks.

The service reuses the selected model, releases it after inactivity, and cancels
or restarts failed worker processes without taking down the desktop. Automatic
selection measures CPU thread counts and available GPU acceleration locally,
preserving model quality. Users can override or repeat selection. Diagnostics
contain bounded timing metadata, not recordings or recognized text.

## Live transcription policy

Live transcription defaults to Automatic. On and Off override the automatic
decision. Explicit legacy boolean preferences remain effective until the user
chooses a new mode; opening settings never migrates or rewrites preferences.

Automatic requires at least two successful warm measurements for the selected
backend and thread count, each taking at most 300 ms and at most 2.5% of the
sample duration. These conservative bounds reserve time for repeated inference
on phrases growing to 12 seconds, with previews requested every 0.8 seconds.
They are heuristics, not a guarantee that short-sample performance scales
linearly. A real preview taking over 400 ms disables automatic previews for the
rest of that session; final recognition continues. Explicit On is not overridden.

The decision is recalculated from validated cached measurements. Missing,
incomplete or stale measurements, failed tuning, and manual backend selection
without measurements leave automatic previews off. The calibration microphone
remains off. Changing model or environment invalidates the measurement cache.

Final-result preview remains an independent display preference. Noise reduction
remains off by default; performance calibration cannot evaluate the user's room
or microphone. Compare real microphone recordings and recognition quality with
and without DSP before considering a change to that default.

Build with `apkg build --all`. Run `apkg test --profile anduinos-package-release-test`
for acceptance; see [testing guidance](docs/testing.md) for dependencies, GPU
checks and limitations. Generated measurements belong in the ignored
`anduinos-whisper-framework/obj/voice-test-results/` directory, not in source control.
