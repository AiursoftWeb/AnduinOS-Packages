"""Language definitions and translations for the AnduinOS installer.

This module is the single source of truth for supported language metadata,
replacing the ubiquity-languagelist / generate-languagelist-data.py pipeline.
User-visible interface text is translated through the gettext catalogs.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re


# ---------------------------------------------------------------------------
# Language metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Language:
    """A language that the installer supports."""
    code: str           # IETF language tag, e.g. "zh_CN"
    english_name: str   # "Chinese (Simplified)"
    native_name: str    # "中文(简体)"
    locale: str         # "zh_CN.UTF-8"
    keyboard: str       # default physical XKB layout, e.g. "us"


# Sorted alphabetically by English name (matching the existing .data file order).
# Data sourced from anduinos-installer-config/assets/ubiquity-languagelist
# and generate-languagelist-data.py.
LANGUAGES = [
    Language("ar",    "Arabic",                "العربية",              "ar_SA.UTF-8", "ara"),
    # Chinese text input is provided by IBus/Rime. Chinese locales normally
    # use a standard US physical keyboard and must not be mapped from country
    # codes to unrelated XKB layout identifiers.
    Language("zh_CN", "Chinese (Simplified)",  "中文(简体)",           "zh_CN.UTF-8", "us"),
    Language("zh_HK", "Chinese (Hong Kong)",   "中文 (香港)",          "zh_HK.UTF-8", "us"),
    Language("zh_TW", "Chinese (Traditional)", "中文(繁體)",           "zh_TW.UTF-8", "us"),
    Language("da",    "Danish",                "Dansk",                "da_DK.UTF-8", "dk"),
    Language("nl",    "Dutch",                 "Nederlands",           "nl_NL.UTF-8", "nl"),
    Language("en_US", "English (United States)", "English (United States)", "en_US.UTF-8", "us"),
    Language("en_GB", "English (United Kingdom)", "English (United Kingdom)", "en_GB.UTF-8", "gb"),
    Language("fi",    "Finnish",               "Suomi",                "fi_FI.UTF-8", "fi"),
    Language("fr",    "French",                "Français",             "fr_FR.UTF-8", "fr"),
    Language("de",    "German",                "Deutsch",              "de_DE.UTF-8", "de"),
    Language("el",    "Greek",                 "Ελληνικά",             "el_GR.UTF-8", "gr"),
    Language("hi",    "Hindi",                 "हिन्दी",               "hi_IN.UTF-8", "in"),
    Language("id",    "Indonesian",            "Bahasa Indonesia",     "id_ID.UTF-8", "id"),
    Language("it",    "Italian",               "Italiano",             "it_IT.UTF-8", "it"),
    Language("ja",    "Japanese",              "日本語",               "ja_JP.UTF-8", "jp"),
    Language("ko",    "Korean",                "한국어",               "ko_KR.UTF-8", "kr"),
    Language("pl",    "Polish",                "Polski",               "pl_PL.UTF-8", "pl"),
    Language("pt",    "Portuguese",            "Português",            "pt_PT.UTF-8", "pt"),
    Language("pt_BR", "Portuguese (Brazil)",   "Português do Brasil",  "pt_BR.UTF-8", "br"),
    Language("ro",    "Romanian",              "Română",               "ro_RO.UTF-8", "ro"),
    Language("ru",    "Russian",               "Русский",              "ru_RU.UTF-8", "ru"),
    Language("es",    "Spanish",               "Español",              "es_ES.UTF-8", "es"),
    Language("sv",    "Swedish",               "Svenska",              "sv_SE.UTF-8", "se"),
    Language("th",    "Thai",                  "ภาษาไทย",              "th_TH.UTF-8", "th"),
    Language("tr",    "Turkish",               "Türkçe",               "tr_TR.UTF-8", "tr"),
    Language("uk",    "Ukrainian",             "Українська",           "uk_UA.UTF-8", "ua"),
    Language("vi",    "Vietnamese",            "Tiếng Việt",           "vi_VN.UTF-8", "vn"),
]

# Installer-owned defaults. These are intentionally keyed by supported
# language/region rather than inferred from the live machine's network.
DEFAULT_TIMEZONES = {
    "ar": "Asia/Riyadh",
    "zh_CN": "Asia/Shanghai",
    "zh_HK": "Asia/Hong_Kong",
    "zh_TW": "Asia/Taipei",
    "da": "Europe/Copenhagen",
    "nl": "Europe/Amsterdam",
    "en_US": "America/New_York",
    "en_GB": "Europe/London",
    "fi": "Europe/Helsinki",
    "fr": "Europe/Paris",
    "de": "Europe/Berlin",
    "el": "Europe/Athens",
    "hi": "Asia/Kolkata",
    "id": "Asia/Jakarta",
    "it": "Europe/Rome",
    "ja": "Asia/Tokyo",
    "ko": "Asia/Seoul",
    "pl": "Europe/Warsaw",
    "pt": "Europe/Lisbon",
    "pt_BR": "America/Sao_Paulo",
    "ro": "Europe/Bucharest",
    "ru": "Europe/Moscow",
    "es": "Europe/Madrid",
    "sv": "Europe/Stockholm",
    "th": "Asia/Bangkok",
    "tr": "Europe/Istanbul",
    "uk": "Europe/Kyiv",
    "vi": "Asia/Ho_Chi_Minh",
}


def default_timezone(code: str) -> str:
    """Return the maintained representative timezone for a language."""
    return DEFAULT_TIMEZONES.get(code, "America/New_York")


def is_chinese(code: str) -> bool:
    """Return True if the language code is a Chinese variant."""
    return code.startswith("zh_")


_LANGUAGE_BY_CODE = {language.code: language for language in LANGUAGES}
_LANGUAGE_BY_LOCALE = {
    language.locale.removesuffix(".UTF-8"): language for language in LANGUAGES
}
_LOCALE_ASSIGNMENT_RE = re.compile(
    r"""^\s*(?:export\s+)?(?P<key>LC_ALL|LC_MESSAGES|LANG)\s*=\s*
        (?P<quote>["']?)(?P<value>[^"'#\s]+)(?P=quote)\s*(?:\#.*)?$""",
    re.VERBOSE,
)


def language_for_locale(locale_name: str | None) -> Language | None:
    """Map a locale spelling to one of the installer-supported languages."""
    if not locale_name:
        return None
    normalized = locale_name.strip().replace("-", "_")
    normalized = normalized.split("@", 1)[0].split(".", 1)[0]
    if not normalized or normalized.upper() in {"C", "POSIX"}:
        return None

    # Preserve the explicitly supported regional variants first.
    exact = _LANGUAGE_BY_LOCALE.get(normalized)
    if exact is not None:
        return exact
    exact = _LANGUAGE_BY_CODE.get(normalized)
    if exact is not None:
        return exact

    language, _, territory = normalized.partition("_")
    language = language.lower()
    territory = territory.upper()
    if language == "zh":
        if territory == "HK":
            return _LANGUAGE_BY_CODE["zh_HK"]
        if territory in {"TW", "MO"}:
            return _LANGUAGE_BY_CODE["zh_TW"]
        return _LANGUAGE_BY_CODE["zh_CN"]
    if language == "pt" and territory == "BR":
        return _LANGUAGE_BY_CODE["pt_BR"]
    if language == "en":
        return _LANGUAGE_BY_CODE[
            "en_GB" if territory == "GB" else "en_US"
        ]
    return _LANGUAGE_BY_CODE.get(language)


def detect_system_language(
    environ: Mapping[str, str] | None = None,
    locale_file: Path = Path("/etc/default/locale"),
) -> Language:
    """Detect the Live session language, with a deterministic English fallback."""
    environment = os.environ if environ is None else environ
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        language = language_for_locale(environment.get(key))
        if language is not None:
            return language

    try:
        assignments = _read_locale_assignments(locale_file)
    except OSError:
        assignments = {}
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        language = language_for_locale(assignments.get(key))
        if language is not None:
            return language
    return _LANGUAGE_BY_CODE["en_US"]


def _read_locale_assignments(path: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LOCALE_ASSIGNMENT_RE.fullmatch(line)
        if match:
            assignments[match.group("key")] = match.group("value")
    return assignments


# Chinese mirror URLs (tried in order; first is the default).
CHINESE_MIRRORS = [
    "https://mirrors.tuna.tsinghua.edu.cn/ubuntu/",
    "https://mirrors.aliyun.com/ubuntu/",
    "https://mirrors.ustc.edu.cn/ubuntu/",
]
