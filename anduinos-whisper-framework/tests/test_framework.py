from pathlib import Path
from array import array
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from anduinos_whisper_framework.commands import (  # noqa: E402
    apply_voice_command,
    clean_transcript,
    remove_punctuation,
)
from anduinos_whisper_framework.chinese import (  # noqa: E402
    normalize_chinese_script,
    whisper_language,
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "benchmarks"))
from benchmark_engine import WhisperEngine
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
        self.assertEqual(apply_voice_command("停止聽寫。", True), ("", "stop"))

    def test_voice_punctuation_command_survives_disabled_auto_punctuation(self):
        without_punctuation = remove_punctuation("Comma.")
        self.assertEqual(apply_voice_command(without_punctuation, True), (",", None))
        self.assertEqual(apply_voice_command("逗號。", True), ("，", None))

    def test_non_speech_annotations_are_removed(self):
        self.assertEqual(clean_transcript("  [BLANK_AUDIO] Hello  world "), "Hello world")

class EngineTests(unittest.TestCase):
    @patch("benchmark_engine.subprocess.run")
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

    @patch("benchmark_engine.subprocess.run")
    def test_engine_forces_simplified_chinese_output(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 0, stdout="語音輸入與電腦", stderr=""
        )
        with patch("pathlib.Path.is_file", return_value=True):
            text = WhisperEngine(Path("/model.bin"), "zh-Hans", 3).transcribe(
                b"\0" * 32_000
            )
        self.assertEqual(text, "语音输入与电脑")
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--language") + 1], "zh")

class ChineseScriptTests(unittest.TestCase):
    def test_simplified_selection_removes_traditional_variants(self):
        self.assertEqual(
            normalize_chinese_script("語音輸入與電腦", "zh-Hans"),
            "语音输入与电脑",
        )
        self.assertEqual(
            normalize_chinese_script("語音輸入與電腦", "zh"),
            "语音输入与电脑",
        )

    def test_traditional_selection_removes_simplified_variants(self):
        self.assertEqual(
            normalize_chinese_script("语音输入与电脑", "zh-Hant"),
            "語音輸入與電腦",
        )

    def test_non_chinese_languages_are_untouched(self):
        self.assertEqual(normalize_chinese_script("語音 input", "en"), "語音 input")
        self.assertEqual(whisper_language("zh-Hans"), "zh")
        self.assertEqual(whisper_language("zh-Hant"), "zh")
        self.assertEqual(whisper_language("en"), "en")

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
            partial_interval=0.5,
        )
        loud_audio = array("h", [12_000] * 1_600).tobytes()
        timestamps = [index / 10 for index in range(13)]
        with patch(
            "anduinos_whisper_framework.audio.time.monotonic",
            side_effect=timestamps,
        ):
            for _timestamp in timestamps:
                capture._consume(loud_audio, voiced=True)
        self.assertEqual(len(partials), 2)
        self.assertGreaterEqual(
            len(partials[0]), AudioCapture.BYTES_PER_SECOND // 2
        )

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

    def test_source_payload_has_no_python_cache_files(self):
        self.assertEqual(list((ROOT / "src").rglob("*.pyc")), [])
        self.assertEqual(list((ROOT / "src").rglob("__pycache__")), [])

    def test_non_shell_clients_cannot_control_dictation(self):
        service = VoiceTypingService.__new__(VoiceTypingService)
        service.shell_owner = ":1.42"
        invocation = Mock()
        service._method_called(
            Mock(),
            ":1.7",
            "/com/anduinos/VoiceTyping",
            "com.anduinos.VoiceTyping",
            "Start",
            Mock(),
            invocation,
        )
        invocation.return_dbus_error.assert_called_once()
        self.assertIn("AccessDenied", invocation.return_dbus_error.call_args.args[0])

if __name__ == "__main__":
    unittest.main()
