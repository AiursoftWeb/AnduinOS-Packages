#!/usr/bin/env python3
"""Regression checks for the explicit voice quality-gate dependency."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[3] / "lib/verify-ci-package-needs.py"
spec = importlib.util.spec_from_file_location("package_needs", path)
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)


class VoiceGateTests(unittest.TestCase):
    def check(self, content):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "ci.yml"
            fixture.write_text(content)
            with patch.object(policy, "CI_PATH", fixture):
                result = policy.verify()
                worker = policy.jobs()["anduinos-whisper-worker"]
                if worker.quality_gate != "voice-cpu-acceptance":
                    raise RuntimeError("Missing voice quality-gate declaration")
                if "voice-cpu-acceptance" not in worker.needs:
                    raise RuntimeError("missing=voice-cpu-acceptance")
                return result

    def test_current_graph_is_valid(self):
        self.check(policy.CI_PATH.read_text())

    def test_removing_publish_gate_fails(self):
        content = policy.CI_PATH.read_text().replace("    - voice-cpu-acceptance\n", "")
        with self.assertRaisesRegex(RuntimeError, "missing=voice-cpu-acceptance"):
            self.check(content)

    def test_missing_gate_job_fails(self):
        content = policy.CI_PATH.read_text().replace("voice-cpu-acceptance:\n", "renamed-quality-job:\n")
        with self.assertRaisesRegex(RuntimeError, "Missing required CI jobs"):
            self.check(content)

    def test_missing_voice_declaration_fails(self):
        content = policy.CI_PATH.read_text().replace(
            "    QUALITY_GATE: voice-cpu-acceptance\n", ""
        ).replace("    - voice-cpu-acceptance\n", "")
        with self.assertRaisesRegex(RuntimeError, "Missing voice quality-gate declaration"):
            self.check(content)

    def test_package_cannot_be_disguised_as_quality_gate(self):
        content = policy.CI_PATH.read_text().replace(
            "    QUALITY_GATE: voice-cpu-acceptance", "    QUALITY_GATE: apkg")
        with self.assertRaisesRegex(RuntimeError, "Quality gate must not publish a package"):
            self.check(content)

    def test_unrelated_extra_dependency_is_still_rejected(self):
        content = policy.CI_PATH.read_text().replace(
            "    - voice-cpu-acceptance\n", "    - voice-cpu-acceptance\n    - apkg\n")
        with self.assertRaisesRegex(RuntimeError, "extra=apkg"):
            self.check(content)


if __name__ == "__main__":
    unittest.main()
