"""Language definitions and translations for the AnduinOS installer.

This module is the single source of truth for supported language metadata,
replacing the ubiquity-languagelist / generate-languagelist-data.py pipeline.
User-visible interface text is translated through the gettext catalogs.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any


# ---------------------------------------------------------------------------
# Language metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DesktopInputSource:
    """One desktop input source to enable for newly created users."""

    type: str
    id: str


@dataclass(frozen=True)
class InputMethod:
    """Installer-owned, fully declarative input-method policy."""

    id: str
    display_name: str
    language_name: str
    desktop_source: DesktopInputSource | None
    packages: tuple[str, ...]
    required_paths: tuple[Path, ...]


@dataclass(frozen=True)
class Language:
    """A language that the installer supports."""
    code: str           # IETF language tag, e.g. "zh_CN"
    english_name: str   # "Chinese (Simplified)"
    native_name: str    # "中文(简体)"
    locale: str         # "zh_CN.UTF-8"
    language_pack_code: str  # Ubuntu langpack suffix, e.g. "zh-hans"
    keyboard: str       # default physical XKB layout, e.g. "us"
    timezone: str
    recommended_input_method: str | None = None


_CONFIG_SCHEMA_VERSION = 4
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9+._-]*$")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:@/-]*$")


def _config_path() -> Path:
    module = Path(__file__).resolve()
    source_tree = module.parent.parent / "data/languages.json"
    if source_tree.is_file():
        return source_tree
    unpacked_tree = (
        module.parents[2]
        / "share/anduinos-installer-beta/languages.json"
    )
    if unpacked_tree.is_file():
        return unpacked_tree
    return Path("/usr/share/anduinos-installer-beta/languages.json")


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise RuntimeError(
            f"Invalid {label} fields; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{label} must be a non-empty string")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"{label} must be a non-empty list")
    result = tuple(_nonempty_string(item, label) for item in value)
    if len(result) != len(set(result)):
        raise RuntimeError(f"{label} contains duplicates")
    return result


def _relative_path(value: object, label: str) -> Path:
    raw = _nonempty_string(value, label)
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or raw != str(path)
        or not path.parts
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise RuntimeError(f"Unsafe relative path in {label}")
    return Path(*path.parts)


def _normalize_locale(locale_name: object) -> str:
    if not isinstance(locale_name, str):
        return ""
    normalized = locale_name.strip().replace("-", "_")
    return normalized.split("@", 1)[0].split(".", 1)[0]


def _load_configuration() -> tuple[
    tuple[Language, ...], dict[str, InputMethod], str, dict[str, str],
    dict[str, str], frozenset[str],
]:
    path = _config_path()
    try:
        root = _object(json.loads(path.read_text(encoding="utf-8")), "root")
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Cannot load installer language policy: {path}"
        ) from error
    _exact_fields(
        root,
        {
            "schema_version", "default_language", "locale_aliases",
            "rtl_languages", "keyboard_layouts", "input_methods",
            "languages",
        },
        "root",
    )
    if root["schema_version"] != _CONFIG_SCHEMA_VERSION:
        raise RuntimeError("Unsupported installer language-policy schema")

    methods_data = _object(root["input_methods"], "input_methods")
    raw_keyboard_layouts = _object(
        root["keyboard_layouts"], "keyboard_layouts"
    )
    keyboard_layouts: dict[str, str] = {}
    for layout, raw_name in raw_keyboard_layouts.items():
        if not isinstance(layout, str) or not _TOKEN_RE.fullmatch(layout):
            raise RuntimeError(f"Invalid keyboard layout id: {layout!r}")
        keyboard_layouts[layout] = _nonempty_string(
            raw_name, f"keyboard_layouts.{layout}"
        )
    if not keyboard_layouts:
        raise RuntimeError("keyboard_layouts must not be empty")

    methods: dict[str, InputMethod] = {}
    for method_id, raw_method in methods_data.items():
        if not isinstance(method_id, str) or not _TOKEN_RE.fullmatch(method_id):
            raise RuntimeError(f"Invalid input-method id: {method_id!r}")
        method = _object(raw_method, f"input_methods.{method_id}")
        _exact_fields(
            method,
            {
                "display_name", "language_name", "desktop_source", "packages",
                "required_paths",
            },
            f"input_methods.{method_id}",
        )
        display_name = _nonempty_string(
            method["display_name"], f"{method_id}.display_name"
        )
        language_name = _nonempty_string(
            method["language_name"], f"{method_id}.language_name"
        )
        packages = _string_list(method["packages"], f"{method_id}.packages")
        if any(not _TOKEN_RE.fullmatch(package) for package in packages):
            raise RuntimeError(f"Unsafe input-method token in {method_id}")

        raw_desktop_source = method["desktop_source"]
        desktop_source = None
        if raw_desktop_source is not None:
            source = _object(
                raw_desktop_source, f"input_methods.{method_id}.desktop_source"
            )
            _exact_fields(
                source,
                {"type", "id"},
                f"input_methods.{method_id}.desktop_source",
            )
            source_type = _nonempty_string(
                source["type"], f"{method_id}.desktop_source.type"
            )
            source_id = _nonempty_string(
                source["id"], f"{method_id}.desktop_source.id"
            )
            if (
                not _TOKEN_RE.fullmatch(source_type)
                or not _SOURCE_ID_RE.fullmatch(source_id)
            ):
                raise RuntimeError(
                    f"Unsafe desktop input source in {method_id}"
                )
            desktop_source = DesktopInputSource(source_type, source_id)

        raw_path_values = _string_list(
            method["required_paths"], f"{method_id}.required_paths"
        )
        path_values = tuple(
            _relative_path(item, f"{method_id}.required_paths")
            for item in raw_path_values
        )

        methods[method_id] = InputMethod(
            method_id,
            display_name,
            language_name,
            desktop_source,
            packages,
            path_values,
        )

    languages_data = root["languages"]
    if not isinstance(languages_data, list) or not languages_data:
        raise RuntimeError("languages must be a non-empty list")
    languages: list[Language] = []
    for index, raw_language in enumerate(languages_data):
        language = _object(raw_language, f"languages[{index}]")
        _exact_fields(
            language,
            {
                "code", "english_name", "native_name", "locale", "keyboard",
                "language_pack_code", "timezone", "recommended_input_method",
            },
            f"languages[{index}]",
        )
        values = {
            key: _nonempty_string(language[key], f"languages[{index}].{key}")
            for key in (
                "code", "english_name", "native_name", "locale", "keyboard",
                "language_pack_code", "timezone",
            )
        }
        if not _TOKEN_RE.fullmatch(values["language_pack_code"]):
            raise RuntimeError(
                f"Invalid language-pack code in languages[{index}]"
            )
        if values["keyboard"] not in keyboard_layouts:
            raise RuntimeError(
                f"languages[{index}] references unknown keyboard layout "
                f"{values['keyboard']!r}"
            )
        input_method_id = language["recommended_input_method"]
        if input_method_id is not None and input_method_id not in methods:
            raise RuntimeError(
                f"languages[{index}] references unknown input method "
                f"{input_method_id!r}"
            )
        languages.append(
            Language(**values, recommended_input_method=input_method_id)
        )
    codes = [language.code for language in languages]
    locales = [language.locale for language in languages]
    if len(codes) != len(set(codes)) or len(locales) != len(set(locales)):
        raise RuntimeError("Language codes and locales must be unique")
    default_language = _nonempty_string(
        root["default_language"], "default_language"
    )
    if default_language not in codes:
        raise RuntimeError("default_language references an unknown language")
    rtl_languages = frozenset(
        _string_list(root["rtl_languages"], "rtl_languages")
    )
    unknown_rtl_languages = rtl_languages - set(codes)
    if unknown_rtl_languages:
        raise RuntimeError(
            "rtl_languages references unknown languages: "
            + ", ".join(sorted(unknown_rtl_languages))
        )
    raw_aliases = _object(root["locale_aliases"], "locale_aliases")
    aliases: dict[str, str] = {}
    for alias, language_code in raw_aliases.items():
        normalized_alias = _normalize_locale(alias)
        if not normalized_alias or normalized_alias in aliases:
            raise RuntimeError(f"Invalid or duplicate locale alias: {alias!r}")
        if language_code not in codes:
            raise RuntimeError(
                f"Locale alias {alias!r} references an unknown language"
            )
        aliases[normalized_alias] = language_code
    return (
        tuple(languages), methods, default_language, aliases,
        keyboard_layouts, rtl_languages,
    )


(
    LANGUAGES,
    INPUT_METHODS,
    DEFAULT_LANGUAGE,
    LOCALE_ALIASES,
    KEYBOARD_LAYOUTS,
    RTL_LANGUAGES,
) = (
    _load_configuration()
)
DEFAULT_TIMEZONES = {language.code: language.timezone for language in LANGUAGES}
DEFAULT_LOCALE = next(
    language.locale for language in LANGUAGES
    if language.code == DEFAULT_LANGUAGE
)
DEFAULT_KEYBOARD = next(
    language.keyboard for language in LANGUAGES
    if language.code == DEFAULT_LANGUAGE
)
DEFAULT_TIMEZONE = DEFAULT_TIMEZONES[DEFAULT_LANGUAGE]


def default_timezone(code: str) -> str:
    """Return the maintained representative timezone for a language."""
    return DEFAULT_TIMEZONES.get(code, DEFAULT_TIMEZONES[DEFAULT_LANGUAGE])


def input_method(method_id: str | None) -> InputMethod | None:
    """Resolve a validated installer-owned input-method policy."""
    if method_id is None:
        return None
    return INPUT_METHODS.get(method_id)


def language_pack_packages(language: Language) -> tuple[str, ...]:
    """Return the exact Ubuntu language-support packages for a language."""
    code = language.language_pack_code
    return (
        f"language-pack-{code}",
        f"language-pack-{code}-base",
        f"language-pack-gnome-{code}",
        f"language-pack-gnome-{code}-base",
    )


_LANGUAGE_BY_CODE = {language.code: language for language in LANGUAGES}
_LANGUAGE_BY_LOCALE = {
    language.locale.removesuffix(".UTF-8"): language for language in LANGUAGES
}
_LANGUAGE_BY_ALIAS = {
    alias: _LANGUAGE_BY_CODE[code] for alias, code in LOCALE_ALIASES.items()
}
_LOCALE_ASSIGNMENT_RE = re.compile(
    r"""^\s*(?:export\s+)?(?P<key>LC_ALL|LC_MESSAGES|LANG)\s*=\s*
        (?P<quote>["']?)(?P<value>[^"'#\s]+)(?P=quote)\s*(?:\#.*)?$""",
    re.VERBOSE,
)


def language_for_locale(locale_name: str | None) -> Language | None:
    """Map a locale spelling using only the declarative language policy."""
    if not locale_name:
        return None
    normalized = _normalize_locale(locale_name)
    if not normalized or normalized.upper() in {"C", "POSIX"}:
        return None

    # Preserve the explicitly supported regional variants first.
    exact = _LANGUAGE_BY_LOCALE.get(normalized)
    if exact is not None:
        return exact
    exact = _LANGUAGE_BY_CODE.get(normalized)
    if exact is not None:
        return exact
    alias = _LANGUAGE_BY_ALIAS.get(normalized)
    if alias is not None:
        return alias

    language_code = normalized.partition("_")[0].lower()
    exact_language = _LANGUAGE_BY_CODE.get(language_code)
    if exact_language is not None:
        return exact_language
    return _LANGUAGE_BY_ALIAS.get(language_code)


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
    return _LANGUAGE_BY_CODE[DEFAULT_LANGUAGE]


def _read_locale_assignments(path: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _LOCALE_ASSIGNMENT_RE.fullmatch(line)
        if match:
            assignments[match.group("key")] = match.group("value")
    return assignments
