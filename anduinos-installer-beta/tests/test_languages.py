import tempfile
import unittest
from pathlib import Path

from languages import (
    DEFAULT_TIMEZONES,
    LANGUAGES,
    default_timezone,
    detect_system_language,
    language_for_locale,
)


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

    def test_locale_spellings_map_to_supported_regional_languages(self):
        cases = {
            "zh_CN.UTF-8": ("zh_CN", "us", "Asia/Shanghai"),
            "zh-TW.UTF-8": ("zh_TW", "us", "Asia/Taipei"),
            "zh_HK": ("zh_HK", "us", "Asia/Hong_Kong"),
            "de_DE.UTF-8": ("de", "de", "Europe/Berlin"),
            "fr_FR@euro": ("fr", "fr", "Europe/Paris"),
            "en_GB.UTF-8": ("en_GB", "gb", "Europe/London"),
            "en_AU.UTF-8": ("en", "us", "America/New_York"),
            "pt_BR.UTF-8": ("pt_BR", "br", "America/Sao_Paulo"),
        }
        for locale_name, expected in cases.items():
            with self.subTest(locale_name=locale_name):
                language = language_for_locale(locale_name)
                self.assertIsNotNone(language)
                self.assertEqual(
                    (
                        language.code,
                        language.keyboard,
                        default_timezone(language.code),
                    ),
                    expected,
                )

    def test_environment_precedence_matches_locale_semantics(self):
        language = detect_system_language(
            {
                "LANG": "de_DE.UTF-8",
                "LC_MESSAGES": "fr_FR.UTF-8",
                "LC_ALL": "zh_TW.UTF-8",
            },
            Path("/does/not/exist"),
        )
        self.assertEqual(language.code, "zh_TW")

    def test_locale_file_is_used_when_session_environment_is_unset(self):
        with tempfile.TemporaryDirectory() as directory:
            locale_file = Path(directory) / "locale"
            locale_file.write_text(
                '# Live locale\nLANG="fr_FR.UTF-8"\n',
                encoding="utf-8",
            )
            language = detect_system_language({}, locale_file)
        self.assertEqual(language.code, "fr")

    def test_unknown_or_c_locale_falls_back_to_english(self):
        for locale_name in ("C.UTF-8", "POSIX", "xx_YY.UTF-8", ""):
            with self.subTest(locale_name=locale_name):
                language = detect_system_language(
                    {"LANG": locale_name}, Path("/does/not/exist")
                )
                self.assertEqual(language.code, "en")
