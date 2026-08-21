import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import languages as language_module

from languages import (
    DEFAULT_TIMEZONES,
    INPUT_METHODS,
    LANGUAGES,
    default_timezone,
    detect_system_language,
    language_for_locale,
    language_pack_packages,
)


class LanguageDefaultsTests(unittest.TestCase):
    def test_current_official_languages_remain_supported(self):
        self.assertTrue(
            {
                "ar",
                "zh_CN",
                "zh_HK",
                "zh_TW",
                "da",
                "nl",
                "en_US",
                "en_GB",
                "fi",
                "fr",
                "de",
                "el",
                "hi",
                "id",
                "it",
                "ja",
                "ko",
                "pl",
                "pt",
                "pt_BR",
                "ro",
                "ru",
                "es",
                "sv",
                "th",
                "tr",
                "uk",
                "vi",
            }
            <= {language.code for language in LANGUAGES}
        )

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

    def test_transliteration_defaults_use_us_physical_keyboard(self):
        keyboards = {language.code: language.keyboard for language in LANGUAGES}
        self.assertEqual(keyboards["hi"], "us")
        self.assertEqual(keyboards["vi"], "us")

    def test_input_method_policy_is_installer_owned_and_complete(self):
        self.assertTrue(
            {
                "rime", "cangjie", "chewing", "mozc", "hangul",
                "unikey", "libthai", "wubi", "quick",
                "cangjie5", "libzhuyin", "hindi_itrans",
                "hindi_inscript2",
            }
            <= set(INPUT_METHODS)
        )
        rime = INPUT_METHODS["rime"]
        self.assertEqual(rime.display_name, "AnduinOS Rime")
        self.assertEqual(rime.language_name, "简体中文")
        self.assertEqual(rime.desktop_source.type, "ibus")
        self.assertEqual(rime.desktop_source.id, "rime")
        self.assertEqual(
            set(rime.packages),
            {"anduinos-rime"},
        )
        mapping = {
            language.code: language.recommended_input_methods
            for language in LANGUAGES
        }
        for code, method_ids in {
            "zh_CN": ("rime", "wubi"),
            "zh_HK": ("cangjie", "quick", "cangjie5"),
            "zh_TW": ("chewing", "libzhuyin"),
            "hi": ("hindi_itrans", "hindi_inscript2"),
            "ja": ("mozc",),
            "ko": ("hangul",),
            "th": ("libthai",),
            "vi": ("unikey",),
        }.items():
            with self.subTest(language=code):
                self.assertEqual(mapping[code], method_ids)
                self.assertEqual(
                    next(
                        language.default_input_methods
                        for language in LANGUAGES
                        if language.code == code
                    ),
                    method_ids[:1],
                )
        self.assertTrue(
            {
                Path("usr/share/rime-data/anduinos_defaults.yaml"),
                Path("usr/share/rime-data/default.custom.yaml"),
                Path("usr/share/rime-data/rime_ice.custom.yaml"),
            }
            <= set(rime.required_paths)
        )
        for method in INPUT_METHODS.values():
            with self.subTest(method=method.id):
                self.assertTrue(method.packages)
                self.assertTrue(method.required_paths)
                self.assertIsNotNone(method.desktop_source)
        self.assertEqual(
            {
                method_id
                for language in LANGUAGES
                for method_id in language.recommended_input_methods
            },
            set(INPUT_METHODS),
        )

    def test_every_supported_language_has_a_maintained_timezone(self):
        self.assertEqual(
            set(DEFAULT_TIMEZONES),
            {language.code for language in LANGUAGES},
        )
        self.assertTrue(
            all("/" in timezone for timezone in DEFAULT_TIMEZONES.values())
        )

    def test_every_supported_language_has_language_pack_policy(self):
        for language in LANGUAGES:
            with self.subTest(language=language.code):
                self.assertTrue(language.language_pack_code)
                self.assertEqual(len(language_pack_packages(language)), 4)

    def test_representative_timezone_examples(self):
        self.assertEqual(default_timezone("en_US"), "America/New_York")
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
            "en_AU.UTF-8": ("en_US", "us", "America/New_York"),
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
                self.assertEqual(language.code, "en_US")

    def test_policy_loader_rejects_path_traversal(self):
        policy = json.loads(
            (Path(__file__).parents[1] / "data/languages.json").read_text(
                encoding="utf-8"
            )
        )
        policy["input_methods"]["rime"]["required_paths"][0] = "../unsafe"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "languages.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with patch.object(language_module, "_config_path", return_value=path):
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    language_module._load_configuration()

    def test_policy_loader_rejects_unknown_input_method(self):
        policy = json.loads(
            (Path(__file__).parents[1] / "data/languages.json").read_text(
                encoding="utf-8"
            )
        )
        policy["languages"][0]["recommended_input_methods"] = ["missing"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "languages.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with patch.object(language_module, "_config_path", return_value=path):
                with self.assertRaisesRegex(RuntimeError, "unknown input method"):
                    language_module._load_configuration()

    def test_new_language_and_input_method_require_json_changes_only(self):
        policy = json.loads(
            (Path(__file__).parents[1] / "data/languages.json").read_text(
                encoding="utf-8"
            )
        )
        policy["input_methods"]["example-ime"] = {
            "display_name": "Example IME",
            "language_name": "Example language",
            "desktop_source": {"type": "ibus", "id": "example:engine"},
            "packages": ["ibus-example"],
            "required_paths": ["usr/share/ibus/component/example.xml"],
        }
        policy["keyboard_layouts"]["xx"] = "Example keyboard"
        policy["languages"].append(
            {
                "code": "xx",
                "english_name": "Example language",
                "native_name": "Example language",
                "locale": "xx_XX.UTF-8",
                "language_pack_code": "xx",
                "keyboard": "xx",
                "timezone": "Etc/UTC",
                "recommended_input_methods": ["example-ime", "rime"],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "languages.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with patch.object(language_module, "_config_path", return_value=path):
                (
                    languages,
                    methods,
                    default_language,
                    aliases,
                    keyboards,
                    rtl_languages,
                ) = language_module._load_configuration()

        added_language = languages[-1]
        added_method = methods["example-ime"]
        self.assertEqual(
            added_language.recommended_input_methods,
            ("example-ime", "rime"),
        )
        self.assertEqual(
            added_language.default_input_methods, ("example-ime",)
        )
        self.assertEqual(added_method.packages, ("ibus-example",))
        self.assertEqual(added_method.desktop_source.id, "example:engine")
        self.assertEqual(default_language, "en_US")
        self.assertEqual(aliases["zh_MO"], "zh_TW")
        self.assertEqual(keyboards["xx"], "Example keyboard")
        self.assertEqual(rtl_languages, frozenset({"ar"}))
