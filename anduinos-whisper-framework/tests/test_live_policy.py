import unittest
from unittest.mock import Mock

from anduinos_whisper_framework.live_policy import live_mode


class LivePolicyTests(unittest.TestCase):
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
