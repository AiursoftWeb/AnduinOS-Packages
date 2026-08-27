from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from anduinos_whisper_framework.commands import (  # noqa: E402
    apply_voice_command,
    clean_transcript,
    remove_punctuation,
)
from anduinos_whisper_framework.engine import WhisperEngine  # noqa: E402
from anduinos_whisper_framework.config import MODELS  # noqa: E402


class CommandTests(unittest.TestCase):
    def test_exact_commands_do_not_replace_normal_sentences(self):
        self.assertEqual(apply_voice_command("New line.", True), ("\n", None))
        self.assertEqual(apply_voice_command("换行。", True), ("\n", None))
        self.assertEqual(
            apply_voice_command("Please start a new line", True),
            ("Please start a new line", None),
        )

    def test_stop_command_is_reported_as_an_action(self):
        self.assertEqual(apply_voice_command("Stop listening.", True), ("", "stop"))

    def test_voice_punctuation_command_survives_disabled_auto_punctuation(self):
        without_punctuation = remove_punctuation("Comma.")
        self.assertEqual(apply_voice_command(without_punctuation, True), (",", None))

    def test_non_speech_annotations_are_removed(self):
        self.assertEqual(clean_transcript("  [BLANK_AUDIO] Hello  world "), "Hello world")


class EngineTests(unittest.TestCase):
    @patch("anduinos_whisper_framework.engine.subprocess.run")
    def test_engine_uses_fixed_argument_vector_and_16khz_wav(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, stdout=" hello ", stderr="")
        with patch("pathlib.Path.is_file", return_value=True):
            text = WhisperEngine(Path("/model.bin"), "zh", 3).transcribe(b"\0" * 32_000)
        self.assertEqual(text, "hello")
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[0], "/usr/bin/whisper-cli")
        self.assertIn("--no-timestamps", arguments)
        self.assertEqual(arguments[arguments.index("--language") + 1], "zh")
        self.assertNotIn("shell", run.call_args.kwargs)


class PackageTests(unittest.TestCase):
    def test_model_tiers_have_clear_user_facing_names(self):
        self.assertEqual(MODELS["tiny"].title, "Whisper Tiny")
        self.assertIn("Fastest", MODELS["tiny"].description)
        self.assertEqual(MODELS["base"].title, "Whisper Base")
        self.assertIn("Balanced", MODELS["base"].description)
        self.assertEqual(MODELS["small"].title, "Whisper Small")
        self.assertIn("High accuracy", MODELS["small"].description)

    def test_source_payload_has_no_python_cache_files(self):
        self.assertEqual(list((ROOT / "src").rglob("*.pyc")), [])
        self.assertEqual(list((ROOT / "src").rglob("__pycache__")), [])

    def test_model_download_is_pinned_and_verified(self):
        script = (ROOT / "scripts/download-model.sh").read_text()
        project = (ROOT / "anduinos-whisper-framework.aosproj").read_text()
        self.assertIn(
            'model_sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"',
            script,
        )
        self.assertIn("sha256sum --check --status", script)
        self.assertIn('<TargetArchitectures>all</TargetArchitectures>', project)
        self.assertIn('<Dependency Include="whisper.cpp (&gt;= 1.8.3)" />', project)

    def test_toggle_has_only_running_and_ready_states(self):
        daemon = (ROOT / "src/anduinos_whisper_framework/daemon.py").read_text()
        self.assertNotIn("self.paused", daemon)
        self.assertNotIn('_set_state("paused"', daemon)
        self.assertIn('elif method in {"Pause", "Stop"}:', daemon)
        self.assertIn('self._set_state("idle", "Ready")', daemon)
        self.assertIn("session_id != self.session_id", daemon)


if __name__ == "__main__":
    unittest.main()
