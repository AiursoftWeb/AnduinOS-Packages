"""Allow-listed performance metadata. Never persist backend logs or dictation.

Missing measurements are omitted, not represented as zero. In particular an
accelerator being discovered does not prove that inference ran on it.
"""
import json
import math


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
