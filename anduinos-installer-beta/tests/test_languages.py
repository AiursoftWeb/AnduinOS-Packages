import unittest

from languages import DEFAULT_TIMEZONES, LANGUAGES, default_timezone


class LanguageDefaultsTests(unittest.TestCase):
    def test_chinese_locales_use_us_physical_keyboard(self):
        chinese = {
            language.code: language.keyboard
            for language in LANGUAGES
            if language.code.startswith("zh_")
        }
        self.assertEqual(
            chinese,
            {"zh_CN": "us", "zh_HK": "us", "zh_TW": "us"},
        )

    def test_every_supported_language_has_a_maintained_timezone(self):
        self.assertEqual(
            set(DEFAULT_TIMEZONES),
            {language.code for language in LANGUAGES},
        )
        self.assertTrue(
            all("/" in timezone for timezone in DEFAULT_TIMEZONES.values())
        )

    def test_representative_timezone_examples(self):
        self.assertEqual(default_timezone("en"), "America/New_York")
        self.assertEqual(default_timezone("zh_CN"), "Asia/Shanghai")
        self.assertEqual(default_timezone("en_GB"), "Europe/London")
        self.assertEqual(default_timezone("unknown"), "America/New_York")
