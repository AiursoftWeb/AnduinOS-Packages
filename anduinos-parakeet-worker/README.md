# AnduinOS Parakeet worker

A private CPU speech-recognition process for optional Parakeet TDT 0.6B v3
INT8 weights. It uses distribution ONNX Runtime and NumPy packages, keeps
weights resident between requests, and exchanges bounded PCM and JSON through
pipes. It has no microphone, network-download, or desktop-input code.

Build with `apkg build --all`. The settings application downloads and verifies
weights separately; this package does not bundle them. See `NOTICE` and
`LICENSE` for attribution and licensing. The framework's optional native test
covers the resident pipe lifecycle with real public audio and staged weights.
