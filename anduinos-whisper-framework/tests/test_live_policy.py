import unittest
from unittest.mock import Mock

from anduinos_whisper_framework.live_policy import live_mode, permits_preview, preview_capable


class LivePolicyTests(unittest.TestCase):
    def test_user_overrides_and_unknown_measurements(self):
        self.assertTrue(permits_preview("on", False))
        self.assertFalse(permits_preview("off", True))
        self.assertFalse(permits_preview("auto", None))
        self.assertTrue(permits_preview("auto", True))

    def test_legacy_choice_is_preserved_without_writing_settings(self):
        settings = Mock()
        settings.get_string.return_value = "auto"
        legacy = Mock()
        legacy.get_boolean.return_value = False
        settings.get_user_value.side_effect = lambda key: legacy if key == "live-transcription" else None
        self.assertEqual(live_mode(settings), "off")
        legacy.get_boolean.return_value = True
        self.assertEqual(live_mode(settings), "on")
        settings.get_user_value.return_value = None
        settings.get_user_value.side_effect = None
        self.assertEqual(live_mode(settings), "auto")
        settings.get_user_value.return_value = Mock()
        self.assertEqual(live_mode(settings), "auto")
        settings.set_string.assert_not_called()
        settings.set_boolean.assert_not_called()

    def test_only_repeated_fast_selected_warm_measurements_qualify(self):
        selected = {"backend": "gpu", "threads": 2}
        fast = {**selected, "phase": "warm", "status": "success",
                "audio_ms": 4000, "inference_ms": 50}
        self.assertTrue(preview_capable([fast, fast], selected))
        for changes in ({"phase": "cold"}, {"backend": "cpu"}, {"threads": 4},
                        {"status": "error"}, {"inference_ms": 500},
                        {"audio_ms": 100}, {"audio_ms": 0},
                        {"inference_ms": float("nan")}):
            with self.subTest(changes=changes):
                self.assertFalse(preview_capable([fast, {**fast, **changes}], selected))
        self.assertFalse(preview_capable([fast], selected))
        self.assertFalse(preview_capable([], selected))
