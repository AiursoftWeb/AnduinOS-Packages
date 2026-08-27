from pathlib import Path
from array import array
import subprocess
import sys
import threading
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch


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
from anduinos_whisper_framework.audio import AudioCapture  # noqa: E402
from anduinos_whisper_framework.daemon import VoiceTypingService  # noqa: E402


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


class LiveTranscriptionTests(unittest.TestCase):
    def test_capture_publishes_accumulated_audio_once_per_interval(self):
        partials = []
        capture = AudioCapture(
            microphone="",
            on_chunk=lambda _pcm: None,
            on_partial=partials.append,
            on_level=lambda _level: None,
            on_error=lambda _message: None,
            on_no_speech=lambda: None,
        )
        loud_audio = array("h", [12_000] * 1_600).tobytes()
        timestamps = [index / 10 for index in range(11)]
        with patch(
            "anduinos_whisper_framework.audio.time.monotonic",
            side_effect=timestamps,
        ):
            for _timestamp in timestamps:
                capture._consume(loud_audio)
        self.assertEqual(len(partials), 1)
        self.assertGreaterEqual(len(partials[0]), AudioCapture.BYTES_PER_SECOND)

    def test_running_partial_remains_valid_but_queued_old_partial_is_skipped(self):
        service = VoiceTypingService.__new__(VoiceTypingService)
        service.work_lock = threading.Lock()
        service.partial_generation = 0
        service.partial_floor = 0
        service.session_id = 7
        service.active = True
        service.settings = Mock()
        service.settings.get_boolean.return_value = True

        first = service._next_partial()
        second = service._next_partial()
        self.assertFalse(service._partial_should_run(7, first))
        self.assertTrue(service._partial_should_run(7, second))
        self.assertTrue(service._partial_is_valid(7, first))

        service._invalidate_partials()
        self.assertFalse(service._partial_is_valid(7, first))
        self.assertFalse(service._partial_is_valid(7, second))


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

    def test_live_transcription_is_default_and_partial_results_are_not_final(self):
        schema = ET.parse(
            ROOT / "data/com.anduinos.voice-typing.gschema.xml"
        ).getroot()
        live_key = schema.find(".//key[@name='live-transcription']")
        self.assertIsNotNone(live_key)
        self.assertEqual(live_key.findtext("default"), "true")

        audio = (ROOT / "src/anduinos_whisper_framework/audio.py").read_text()
        daemon = (ROOT / "src/anduinos_whisper_framework/daemon.py").read_text()
        self.assertIn("partial_interval: float = 1.0", audio)
        self.assertIn("self.on_partial(partial)", audio)
        self.assertIn("queue.PriorityQueue", daemon)
        self.assertIn('self._put_work(0, "final"', daemon)
        self.assertIn('self._put_work(1, "partial"', daemon)
        self.assertIn('GLib.Variant("(sb)", (text, False))', daemon)
        self.assertIn("_partial_should_run", daemon)
        self.assertIn("_partial_is_valid", daemon)


if __name__ == "__main__":
    unittest.main()
