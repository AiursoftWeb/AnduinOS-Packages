#!/usr/bin/python3
"""Offline CLI/resident regression on the pinned public corpus, never a mic.

Prints JSON to stdout. Exit 1 when resident error counts exceed the matching
CLI baseline. Performance measurements are observations, not cross-device
latency guarantees. Uses the same model and beam defaults throughout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_engine import WhisperEngine
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.calibration_audio import noisy


def units(text, language):
    text = unicodedata.normalize("NFKC", text).casefold()
    if language.startswith("zh"):
        return [c for c in text if c.isalnum()]
    return re.findall(r"[^\W_]+", text)


def edit_distance(expected, actual):
    row = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        next_row = [i]
        for j, right in enumerate(actual, 1):
            next_row.append(min(row[j] + 1, next_row[-1] + 1, row[j-1] + (left != right)))
        row = next_row
    return row[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", default="/usr/libexec/anduinos-whisper-worker")
    parser.add_argument("--model", type=Path, default=Path("/usr/share/anduinos-whisper-framework/models/ggml-base.bin"))
    parser.add_argument("--gpu", action="store_true", help="Also test an actual GPU candidate")
    parser.add_argument("--compare-cli-gpu", action="store_true", help="Add CLI GPU diagnosis; CPU accuracy baseline remains unchanged")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--no-fallback", action="store_true", help="Experiment: disable resident temperature fallback only")
    parser.add_argument("--beam", type=int, choices=(1, 2, 3, 4, 5), default=5,
                        help="Experimental resident beam size; CLI baseline stays at 5")
    parser.add_argument("--no-flash-attn", action="store_true", help="Experiment: disable resident flash attention")
    args = parser.parse_args()
    directory = ROOT / "data" / "benchmark"
    manifest = json.loads((directory / "manifest.json").read_text())
    results, engines = [], {}
    passed = True
    try:
        for sample in manifest["samples"]:
            path = directory / sample["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != sample["sha256"]:
                raise RuntimeError("Public corpus checksum mismatch")
            with wave.open(str(path), "rb") as audio:
                if (audio.getframerate(), audio.getsampwidth(), audio.getnchannels()) != (16000, 2, 1):
                    raise RuntimeError("Invalid public sample format")
                pcm = audio.readframes(audio.getnframes())
            language = sample["language"]
            expected = units(sample["text"], language)
            for condition, data in (("clean", pcm), ("white-noise-20dB-SNR", noisy(pcm))):
                baseline_errors = None
                modes = ["cli-cpu", "resident-cpu"]
                if args.gpu:
                    modes.append("resident-gpu")
                if args.compare_cli_gpu:
                    modes.append("cli-gpu")
                for mode in modes:
                    key = (mode, language)
                    if key not in engines:
                        engines[key] = (WhisperEngine(args.model, language, args.threads, backend=mode.split("-")[1])
                                        if mode.startswith("cli-") else
                                        ResidentEngine(args.model, language, args.threads,
                                                       backend=mode.split("-")[1], executable=args.worker,
                                                       beam=args.beam,
                                                       no_fallback=args.no_fallback))
                        if mode.startswith("resident-"):
                            engines[key].no_flash_attn = args.no_flash_attn
                    engine = engines[key]
                    started = time.monotonic()
                    text = engine.transcribe(data)
                    elapsed_ms = (time.monotonic() - started) * 1000
                    errors = edit_distance(expected, units(text, language))
                    if mode == "cli-cpu":
                        baseline_errors = errors
                    eligible = not mode.endswith("-gpu") or engine.last_metrics.get("backend") == "gpu"
                    if eligible and baseline_errors is not None and errors > baseline_errors:
                        passed = False
                    results.append({"sample": sample["file"], "condition": condition, "mode": mode,
                                    "actual_backend": engine.last_metrics.get("backend"),
                                    "hardware_exercised": eligible, "errors": errors, "units": len(expected),
                                    "metric": "CER" if language.startswith("zh") else "WER",
                                    "wall_ms": round(elapsed_ms, 3), "metrics": engine.last_metrics})
    finally:
        for engine in engines.values():
            close = getattr(engine, "close", None)
            if close:
                close()
    print(json.dumps({"schema_version": 1, "corpus_version": manifest["version"],
                      "resident_temperature_fallback": not args.no_fallback,
                      "resident_beam": args.beam,
                      "resident_flash_attention": not args.no_flash_attn,
                      "resident_fresh_state": True,
                      "threads": args.threads, "gpu_requested": args.gpu,
                      "cli_gpu_requested": args.compare_cli_gpu,
                      "accuracy_nonregression": passed, "results": results}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
