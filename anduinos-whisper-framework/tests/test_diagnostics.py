"""The GTK client's privacy boundary remains Python; test the shipped helper."""
import json
import unittest

from anduinos_whisper_framework.diagnostics import sanitize, sanitize_report


class DiagnosticsTests(unittest.TestCase):
    def test_export_discards_private_and_invalid_fields(self):
        raw = json.dumps({"schema_version": 1, "measurements": [{
            "text": "secret", "error": "secret", "backend": "secret",
            "threads": 4, "queue_ms": 12.3456, "encode_ms": -1,
        }]})
        self.assertEqual(json.loads(sanitize_report(raw)), {
            "schema_version": 1, "measurements": [{"threads": 4, "queue_ms": 12.346}]})
        self.assertEqual(sanitize({"queue_ms": True, "load_ms": float("nan"),
                                   "decode_ms": float("inf"), "threads": 0}), {})

    def test_rejects_invalid_or_oversized_reports(self):
        for raw in ("x" * 131073, "not json", "[]",
                    '{"schema_version":2,"measurements":[]}',
                    '{"schema_version":1,"measurements":[null]}',
                    json.dumps({"schema_version": 1, "measurements": [{}] * 101})):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                sanitize_report(raw)
