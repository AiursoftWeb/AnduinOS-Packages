#!/usr/bin/python3
"""Check automatically selected configurations against CPU CLI accuracy.

Uses only the pinned public corpus. Tests auto-language plus explicit English
and Chinese modes. No transcript is printed or retained in the report.
"""
import argparse
from functools import partial
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import wave

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("corpus_reference", ROOT / "scripts/benchmark-corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.tuning import AutomaticSelector, BackendTuner, SelectionCache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--model", type=Path, default=Path("/usr/share/anduinos-whisper-framework/models/ggml-base.bin"))
    args = parser.parse_args()
    directory = ROOT / "data/benchmark"
    manifest = json.loads((directory / "manifest.json").read_text())
    results, selections = [], []
    passed = True
    factory = partial(ResidentEngine, executable=args.worker)
    with tempfile.TemporaryDirectory(prefix="anduinos-selected-corpus-") as temporary:
        selector = AutomaticSelector(directory, SelectionCache(Path(temporary) / "selection.json"),
                                     BackendTuner(factory), args.worker)
        for mode in ("auto", "en", "zh-Hans"):
            before = time.monotonic()
            choice = selector.select(args.model, language=mode, force=True)
            selections.append({"language_mode": mode, **choice, "status": selector.status,
                               "calibration_ms": round((time.monotonic() - before) * 1000, 3)})
            # A fallback can be safe, but does not prove measured selection works.
            passed &= selector.status == "measured"
            reference = corpus.WhisperEngine(args.model, mode, 4, backend="cpu")
            with factory(args.model, mode, **choice) as engine:
                for sample in manifest["samples"]:
                    if mode != "auto" and sample["language"] != mode:
                        continue
                    path = directory / sample["file"]
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == sample["sha256"]
                    with wave.open(str(path), "rb") as audio:
                        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
                        pcm = audio.readframes(audio.getnframes())
                    expected = corpus.units(sample["text"], sample["language"])
                    for condition, data in (("clean", pcm), ("noisy", corpus.noisy(pcm))):
                        baseline = reference.transcribe(data)
                        before = time.monotonic()
                        text = engine.transcribe(data)
                        wall = (time.monotonic() - before) * 1000
                        baseline_errors = corpus.edit_distance(expected, corpus.units(baseline, sample["language"]))
                        errors = corpus.edit_distance(expected, corpus.units(text, sample["language"]))
                        actual = engine.last_metrics.get("backend")
                        passed &= bool(text) and errors <= baseline_errors and actual == choice["backend"]
                        results.append({"language_mode": mode, "sample": sample["file"],
                            "condition": condition, "baseline_errors": baseline_errors,
                            "selected_errors": errors, "units": len(expected), "actual_backend": actual,
                            "threads": choice["threads"], "wall_ms": round(wall, 3)})
            print(json.dumps({"completed_language_mode": mode}), file=sys.stderr, flush=True)
    print(json.dumps({"passed": bool(passed), "selections": selections, "results": results}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
