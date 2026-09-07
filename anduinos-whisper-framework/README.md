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

Build with `apkg build --all`. Run the repository's `bash anduinos-whisper-framework/tests/run-cpu.sh`
for acceptance; see [testing guidance](docs/testing.md) for dependencies, GPU
checks and limitations. Generated measurements belong in the ignored
`anduinos-whisper-framework/obj/voice-test-results/` directory, not in source control.
