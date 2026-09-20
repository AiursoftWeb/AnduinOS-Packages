"""Allow-listed performance metadata. Never persist backend logs or dictation.

Missing measurements are omitted, not represented as zero. In particular an
accelerator being discovered does not prove that inference ran on it.
"""
from collections import deque
import json
import math
import re
import threading


TIMING_FIELDS = frozenset({
    "audio_ms", "endpoint_ms", "queue_ms", "initialization_ms", "load_ms",
    "mel_ms", "encode_ms", "decode_ms", "batch_decode_ms", "sample_ms",
    "inference_ms", "delivery_ms", "peak_rss_mib",
    "state_initialization_ms", "state_release_ms",
    "prompt_decode_ms",
})
ENUM_FIELDS = {
    "endpoint_reason": {"silence", "max-duration", "finish"},
    "phase": {"cold", "warm"},
    "kind": {"final", "partial", "benchmark"},
    "backend": {"cpu", "gpu", "unknown"},
    "model": {"tiny", "base", "small"},
    "status": {"success", "cancelled", "timeout", "error"},
    "engine": {"cli", "resident", "vad"},
    "fallback": {"gpu_failed"},
}
_TIMINGS = {
    "load": "load_ms", "mel": "mel_ms", "encode": "encode_ms",
    "decode": "decode_ms", "batchd": "batch_decode_ms", "sample": "sample_ms",
}


def parse_cli_backend(stderr: str) -> str:
    """Report activation, not device enumeration; never return driver names."""
    if any(line.startswith("whisper_backend_init_gpu: using ") for line in stderr.splitlines()):
        return "gpu"
    return "unknown"


def parse_cli_timings(stderr: str) -> dict:
    """Extract upstream numeric timing lines; discard every other log line."""
    result = {}
    for name, value in re.findall(
        r"whisper_print_timings:\s+(load|mel|encode|decode|batchd|sample) time\s*=\s*([\d.]+) ms",
        stderr,
    ):
        try:
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number) and number >= 0:
            result[_TIMINGS[name]] = number
    return result


def sanitize(record: dict) -> dict:
    result = {}
    for key in TIMING_FIELDS:
        value = record.get(key)
        if type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 86_400_000:
            result[key] = round(value, 3)
    for key, allowed in ENUM_FIELDS.items():
        value = record.get(key)
        if isinstance(value, str) and value in allowed:
            result[key] = value
    if type(record.get("threads")) is int and 1 <= record["threads"] <= 256:
        result["threads"] = record["threads"]
    return result


def sanitize_report(raw):
    """Revalidate the privacy boundary when a client exports a bus response."""
    if len(raw) > 131072:
        raise ValueError("Diagnostic report too large")
    report = json.loads(raw)
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("Unsupported diagnostic report")
    records = report.get("measurements")
    if not isinstance(records, list) or len(records) > 100 or any(not isinstance(r, dict) for r in records):
        raise ValueError("Invalid diagnostic measurements")
    return json.dumps({"schema_version": 1, "measurements": [sanitize(r) for r in records]},
                      allow_nan=False, indent=2)


class PerformanceHistory:
    """Bounded, in-memory history; exporting requires an explicit caller action."""

    def __init__(self, limit=100):
        self._records = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._sequence = 0
        self._delivery_started = {}

    def append(self, record):
        with self._lock:
            self._sequence = self._sequence % 0xffffffff + 1
            if len(self._records) == self._records.maxlen:
                self._delivery_started.pop(self._records[0][0], None)
            self._records.append((self._sequence, sanitize(record)))
            return self._sequence

    def ready_for_delivery(self, ticket, started):
        with self._lock:
            if any(key == ticket for key, _ in self._records):
                self._delivery_started[ticket] = started

    def acknowledge_delivery(self, ticket, now):
        with self._lock:
            started = self._delivery_started.pop(ticket, None)
            if started is None:
                return False
            for key, record in self._records:
                if key == ticket:
                    record.update(sanitize({"delivery_ms": (now - started) * 1000}))
                    return True
            return False

    def export_json(self):
        with self._lock:
            records = [dict(record) for _, record in self._records]
        return json.dumps({"schema_version": 1, "measurements": records},
                          ensure_ascii=True, allow_nan=False, indent=2)
