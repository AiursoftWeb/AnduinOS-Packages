"""Small, offline, accuracy-preserving CPU/GPU selection; no hardware-name rules."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time
import unicodedata
import wave

from .diagnostics import sanitize
from .errors import RecognitionCancelled, RecognitionError
from .resident import ResidentEngine
from .calibration_audio import noisy

POLICY_VERSION = 5


def available_threads():
    return max(1, len(os.sched_getaffinity(0)))


def candidates(cpu_count=None):
    count = available_threads() if cpu_count is None else max(1, cpu_count)
    # Establish a moderate CPU baseline before GPU, then explore alternatives.
    # Starting with two threads can consume the whole budget on a slow CPU.
    initial = min(count, 4)
    alternatives = dict.fromkeys(min(count, n) for n in (8, 2))
    return [("cpu", initial), ("gpu", initial)] + [
        ("cpu", n) for n in alternatives if n != initial]


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if c.isalnum())


def environment_fingerprint(model, sample_digest, worker="/usr/libexec/anduinos-whisper-worker"):
    """Hash configuration, not machine IDs; never export raw paths or CPU data."""
    files = {Path(model), Path(worker),
             Path(worker).parent / "anduinos-whisper" / "libanduinos-whisper.so.1"}
    for pattern in ("*/libwhisper.so*", "*/libggml*.so*", "*/ggml/backends0/*.so", "*/libvulkan*.so*",
                    "*/libnvidia*.so*", "*/libcuda.so*"):
        files.update(Path("/usr/lib").glob(pattern))
    files.update(Path("/usr/share/vulkan/icd.d").glob("*.json"))
    identities = []
    for path in sorted(files):
        try:
            stat = path.stat()
            identities.append((str(path), stat.st_size, stat.st_mtime_ns))
        except OSError:
            identities.append((str(path), None))
    cpu = []
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key.strip() in {"model name", "vendor_id", "flags", "Features", "CPU part", "CPU implementer"}:
                cpu.append((key.strip(), value.strip()))
    except OSError:
        pass
    driver = []
    for name in ("/proc/driver/nvidia/version", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"):
        try:
            driver.append(Path(name).read_text()[:8192])
        except OSError:
            pass
    environment = {key: os.environ.get(key, "") for key in (
        "GGML_VK_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "VK_DRIVER_FILES", "VK_ICD_FILENAMES")}
    data = (POLICY_VERSION, platform.machine(), platform.release(), sorted(os.sched_getaffinity(0)),
            identities, sorted(set(cpu)), driver, environment, sample_digest)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def valid_choice(choice):
    return (isinstance(choice, dict) and choice.get("backend") in {"cpu", "gpu"}
            and type(choice.get("threads")) is int
            and 1 <= choice["threads"] <= min(256, available_threads()))


class SelectionCache:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(
            os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
        ) / "anduinos-whisper" / "performance.json"

    def load(self, fingerprint):
        try:
            if self.path.stat().st_size > 65536:
                return None
            record = json.loads(self.path.read_text())
            if (record.get("fingerprint") != fingerprint or
                    record.get("policy") != POLICY_VERSION or
                    not valid_choice(record.get("selected"))):
                return None
            return {k: record["selected"][k] for k in ("backend", "threads")}
        except (OSError, ValueError, AttributeError):
            return None

    def save(self, fingerprint, selected, measurements):
        if not valid_choice(selected):
            raise ValueError("Invalid backend selection")
        record = {"policy": POLICY_VERSION, "fingerprint": fingerprint,
                  "selected": {k: selected[k] for k in ("backend", "threads")},
                  "measurements": [sanitize(r) for r in measurements]}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".performance-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(record, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class BackendTuner:
    def __init__(self, engine_factory=ResidentEngine, clock=time.monotonic):
        self.engine_factory, self.clock = engine_factory, clock
        self.measurements = []

    def run(self, model, pcm, language="en", cancel=None, cpu_count=None,
            additional_pcm=(), threads=0):
        self.measurements = []
        baseline = None
        scored = []
        fastest_clean = None
        deadline = self.clock() + 60
        # One cold request, then repeated clean/noisy checks for every language
        # sample, all using the same language mode and model as live recognition.
        conditions = [audio for sample in (pcm, *additional_pcm)
                      for audio in (sample, noisy(sample))]
        probes = [("cpu", threads), ("gpu", threads)] if threads else candidates(cpu_count)
        for backend, threads in probes:
            if self.clock() >= deadline:
                break
            if cancel is not None and cancel.is_set():
                raise RecognitionCancelled()
            with self.engine_factory(model, language, threads, backend=backend, beam=5, timeout=30) as engine:
                try:
                    outputs, warm = [], []
                    actual_backends = []
                    sequence = [pcm] + [audio for audio in conditions for _ in range(2)]
                    for iteration, audio in enumerate(sequence):
                        if self.clock() >= deadline:
                            raise RecognitionError("Performance measurement budget exhausted")
                        if cancel is not None and cancel.is_set():
                            raise RecognitionCancelled()
                        engine.timeout = max(0.001, min(30, deadline - self.clock()))
                        started = self.clock()
                        text = engine.transcribe(audio, cancel)
                        elapsed = (self.clock() - started) * 1000
                        outputs.append(normalized(text))
                        actual_backends.append(engine.last_metrics.get("backend"))
                        metric = {**engine.last_metrics, "kind": "benchmark", "status": "success",
                                  "phase": "warm" if iteration else "cold"}
                        self.measurements.append(metric)
                        if iteration:
                            warm.append(elapsed)
                        # Failed candidates cannot become eligible later. Stop
                        # before spending more inference on already known failures.
                        if actual_backends[-1] != backend or not outputs[-1]:
                            break
                        if (iteration and baseline is not None and
                                outputs[-1] != baseline[(iteration - 1) // 2]):
                            break
                        if iteration == 1 and outputs[0] != outputs[1]:
                            break
                        if iteration >= 2 and iteration % 2 == 0 and outputs[-1] != outputs[-2]:
                            break
                        # Compare two warm runs of the SAME clean fixture. A
                        # generous margin avoids pruning on small timing noise.
                        # Only a fully quality-validated candidate supplies this
                        # bound; rejected candidates never influence selection.
                        if (iteration == 2 and fastest_clean is not None and
                                statistics.median(warm) > fastest_clean * 1.5):
                            break
                    if len(outputs) != len(sequence):
                        continue
                    # Do not count a failed GPU probe that transparently used CPU
                    # as successful GPU acceleration.
                    if any(actual != backend for actual in actual_backends):
                        continue
                    if (not all(outputs) or outputs[0] != outputs[1] or
                            any(outputs[i] != outputs[i + 1]
                                for i in range(1, len(outputs), 2))):
                        continue
                    signature = tuple(outputs[1::2])
                    if baseline is None:
                        if backend != "cpu":
                            continue
                        baseline = signature
                    if signature != baseline:
                        continue
                    score = statistics.median(warm)
                    if math.isfinite(score) and score > 0:
                        scored.append((score, backend, threads))
                        clean_score = statistics.median(warm[:2])
                        fastest_clean = min(fastest_clean, clean_score) if fastest_clean is not None else clean_score
                except RecognitionError:
                    self.measurements.append({"kind": "benchmark", "status": "error",
                                              "backend": backend, "threads": threads})
        if not scored:
            raise RecognitionError("No reliable performance benchmark result; CPU fallback remains available")
        # Prefer a CPU candidate when improvement is under 10%: tiny timing
        # noise is not a good reason to add GPU memory/power/driver dependency.
        best = min(scored)
        cpu = min((r for r in scored if r[1] == "cpu"), default=None)
        if cpu and best[1] == "gpu" and best[0] > cpu[0] * 0.9:
            best = cpu
        return {"backend": best[1], "threads": best[2]}


class AutomaticSelector:
    """Worker-thread API; callers expose preparing/cancel UI before recording."""
    def __init__(self, directory="/usr/share/anduinos-whisper-framework/benchmark",
                 cache=None, tuner=None, worker="/usr/libexec/anduinos-whisper-worker"):
        self.directory = Path(directory)
        self.cache = cache or SelectionCache()
        self.tuner = tuner or BackendTuner()
        self.worker = worker
        self.failed_fingerprint = None
        self.status = "idle"
        self.measurements = []

    def calibration(self, language="auto"):
        """Return only a checked, public fixture; never capture microphone audio."""
        manifest = json.loads((self.directory / "manifest.json").read_text())
        name = "zh-short.wav" if language.startswith("zh") else "en-short.wav"
        sample = next(s for s in manifest["samples"] if s["file"] == name)
        path = self.directory / name
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Benchmark sample too large")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sample["sha256"]:
            raise ValueError("Benchmark checksum mismatch")
        with wave.open(str(path), "rb") as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                raise ValueError("Invalid benchmark format")
            pcm = audio.readframes(audio.getnframes())
        return pcm, sample

    def select(self, model, language="auto", backend="auto", threads=0, cancel=None,
               force=False, generation=0, on_measure=None):
        # These are only this call's observations, never a replay of disk cache
        # or a previous attempt. Sanitize again before exposing them to callers.
        self.measurements = []
        if backend not in {"auto", "cpu", "gpu"} or type(threads) is not int or not 0 <= threads <= 256:
            raise ValueError("Invalid backend override")
        if type(generation) is not int or not 0 <= generation <= 0xffffffff:
            raise ValueError("Invalid tuning generation")
        if cancel is not None and cancel.is_set():
            raise RecognitionCancelled()
        fallback = {"backend": "cpu", "threads": min(threads or 4, available_threads())}
        if backend != "auto":
            self.status = "manual"
            return {"backend": backend, "threads": min(threads or 4, available_threads())}
        try:
            pcm, sample = self.calibration(language)
            extra = [self.calibration("zh-Hans")] if language == "auto" else []
            digest = ":".join([sample["sha256"], *(s["sha256"] for _, s in extra)])
            fingerprint = environment_fingerprint(model, digest, self.worker)
            measured_threads = min(threads, available_threads()) if threads else 0
            # A persisted settings counter invalidates both disk and in-process
            # failure caches, including after the service has restarted.
            fingerprint = hashlib.sha256(
                f"{fingerprint}:{generation}:{language}:{measured_threads}".encode("utf-8")).hexdigest()
            if not force:
                selected = self.cache.load(fingerprint)
                if selected:
                    self.status = "cached"
                    return selected
                if fingerprint == self.failed_fingerprint:
                    self.status = "fallback"
                    return fallback
            self.status = "measuring"
            if on_measure is not None:
                on_measure()
            try:
                selected = self.tuner.run(model, pcm, language, cancel,
                                          additional_pcm=tuple(audio for audio, _ in extra),
                                          threads=measured_threads)
            except RecognitionError:
                self.failed_fingerprint = fingerprint
                raise
            finally:
                self.measurements = [sanitize(record) for record in self.tuner.measurements[-100:]]
            # A read-only/full cache directory must not disable recognition or
            # discard a valid measured configuration.
            try:
                self.cache.save(fingerprint, selected, self.measurements)
            except OSError:
                pass
            self.status = "measured"
            self.failed_fingerprint = None
            return selected
        except (OSError, ValueError, KeyError, StopIteration, wave.Error, RecognitionError):
            self.status = "fallback"
            return {**fallback, **({"threads": min(threads, available_threads())} if threads else {})}
