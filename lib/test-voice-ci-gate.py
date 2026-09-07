#!/usr/bin/env python3
"""Regression checks for the explicit voice quality-gate dependency."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

path = Path(__file__).with_name("verify-ci-package-needs.py")
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
                return policy.verify()

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

    def test_unrelated_extra_dependency_is_still_rejected(self):
        content = policy.CI_PATH.read_text().replace(
            "    - voice-cpu-acceptance\n", "    - voice-cpu-acceptance\n    - apkg\n")
        with self.assertRaisesRegex(RuntimeError, "extra=apkg"):
            self.check(content)


if __name__ == "__main__":
    unittest.main()
