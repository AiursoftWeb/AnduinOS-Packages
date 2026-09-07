#!/usr/bin/python3
"""Repeated public-corpus inference and cancellation; never records or logs speech.

Reports bounded JSON metrics. RSS growth is measured after warm-up, per worker;
the default 64 MiB budget is a regression guard, not a hardware recommendation.
Accuracy against reference text is covered by benchmark-corpus.py separately.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time
import wave

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.errors import RecognitionCancelled
from anduinos_whisper_framework.calibration_audio import noisy


def resources(pid):
    proc = Path("/proc") / str(pid)
    fields = dict(line.split(":", 1) for line in (proc / "status").read_text().splitlines() if ":" in line)
    return {"rss_mib": int(fields["VmRSS"].split()[0]) / 1024,
            "fds": len(list((proc / "fd").iterdir())), "threads": int(fields["Threads"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--model", type=Path, default=Path("/usr/share/anduinos-whisper-framework/models/ggml-base.bin"))
    parser.add_argument("--backend", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--rss-growth-mib", type=float, default=64)
    args = parser.parse_args()
    if args.requests < 32 or not math.isfinite(args.rss_growth_mib) or args.rss_growth_mib < 0:
        parser.error("Require at least 32 requests and a finite nonnegative growth budget")
    directory = ROOT / "data/benchmark"
    samples = []
    for entry in json.loads((directory / "manifest.json").read_text())["samples"]:
        path = directory / entry["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        with wave.open(str(path), "rb") as audio:
            assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
            pcm = audio.readframes(audio.getnframes())
        for condition, data in (("clean", pcm), ("noisy", noisy(pcm))):
            samples.append((entry["file"], condition, entry["language"], data))
    engines, expected, pids, baseline, observed = {}, {}, {}, {}, {}
    durations, cancellations = [], 0
    started = time.monotonic()
    try:
        for index in range(args.requests):
            name, condition, language, data = samples[index % len(samples)]
            if language not in engines:
                engines[language] = ResidentEngine(args.model, language, 4, args.backend, executable=args.worker)
                observed[language] = []
            engine = engines[language]
            before = time.monotonic()
            text = engine.transcribe(data)
            elapsed = (time.monotonic() - before) * 1000
            assert engine.last_metrics["backend"] == args.backend, "Requested backend was not exercised"
            key = (name, condition)
            if key not in expected:
                expected[key] = text
            assert text and text == expected[key], "Repeated recognition output changed"
            if language not in pids:
                pids[language] = engine.process.pid
            assert engine.process.pid == pids[language], "Worker restarted during continuous phase"
            state = resources(engine.process.pid)
            if index >= len(samples):
                baseline.setdefault(language, state)
                observed[language].append(state)
                durations.append(elapsed)
                assert state["fds"] == baseline[language]["fds"], "File descriptor growth"
                assert state["threads"] == baseline[language]["threads"], "Thread growth"
                assert state["rss_mib"] - baseline[language]["rss_mib"] <= args.rss_growth_mib, (
                    "RSS growth budget exceeded: " + json.dumps({"request": index + 1,
                        "language": language, "baseline": baseline[language], "current": state}))
            if (index + 1) % 20 == 0:
                print(json.dumps({"progress_requests": index + 1,
                    "workers": {lang: {"baseline": baseline.get(lang), "current": rows[-1]}
                                for lang, rows in observed.items() if rows}}), file=sys.stderr, flush=True)

        # After the uninterrupted run, exercise abort/recovery independently so
        # a restart cannot hide leaks in the continuous-phase measurements.
        name, condition, language, data = samples[-2]
        engine = engines[language]
        for _ in range(3):
            cancel = threading.Event()
            timer = threading.Timer(0.01, cancel.set)
            before = time.monotonic()
            timer.start()
            try:
                engine.transcribe(data * 3, cancel)
            except RecognitionCancelled:
                cancellations += 1
            finally:
                timer.join()
            assert time.monotonic() - before < 2, "Cancellation exceeded two seconds"
            assert engine.transcribe(data) == expected[(name, condition)], "Recovery output changed"
        assert cancellations, "No cancellation path was exercised"
        report = {"passed": True, "backend": args.backend, "requests": args.requests,
                  "cancellations": cancellations, "elapsed_s": round(time.monotonic() - started, 2),
                  "warm_median_ms": round(statistics.median(durations), 2),
                  "warm_p95_ms": round(sorted(durations)[math.ceil(len(durations) * .95) - 1], 2),
                  "workers": {language: {"rss_growth_mib": round(max(s["rss_mib"] for s in rows) - baseline[language]["rss_mib"], 2),
                                          "fds": baseline[language]["fds"], "threads": baseline[language]["threads"]}
                              for language, rows in observed.items()}}
    finally:
        for engine in engines.values():
            process = engine.process
            engine.close()
            assert process is None or process.poll() is not None, "Worker was not reaped"
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
