#!/usr/bin/env python3
"""Reject incomplete gettext catalogs for AnduinOS' official languages."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_POLICY = ROOT / "anduinos-installer-beta/data/languages.json"
SOURCE_LANGUAGE = "en_US"


def official_catalog_names() -> dict[str, tuple[str, ...]]:
    policy = json.loads(LANGUAGE_POLICY.read_text(encoding="utf-8"))
    result: dict[str, tuple[str, ...]] = {}
    for language in policy["languages"]:
        code = language["code"]
        locale = language["locale"].split(".", 1)[0]
        result[code] = tuple(dict.fromkeys((code, locale)))
    if policy["default_language"] != SOURCE_LANGUAGE or len(result) != 28:
        raise RuntimeError("The installer must define the 28-language policy")
    return result


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
    )


def missing_languages(
    localized_names: set[str], languages: dict[str, tuple[str, ...]]
) -> list[str]:
    return [
        code
        for code, candidates in languages.items()
        if code != SOURCE_LANGUAGE
        and not any(candidate in localized_names for candidate in candidates)
    ]


def verify_inline_assets(
    languages: dict[str, tuple[str, ...]], errors: list[str]
) -> tuple[int, int]:
    desktop_count = 0
    policy_count = 0
    gettext_packages = {
        template.parent.parent for template in ROOT.glob("*/po/*.pot")
    }
    for package in sorted(gettext_packages):
        for desktop in sorted(package.rglob("*.desktop")):
            if "obj" in desktop.parts:
                continue
            desktop_count += 1
            content = desktop.read_text(encoding="utf-8")
            localized_keys = {
                match.group(1)
                for match in re.finditer(r"^([^=\[\n]+)\[[^]]+\]=", content, re.MULTILINE)
            }
            for key in sorted(localized_keys):
                names = [
                    match.group(1)
                    for match in re.finditer(
                        rf"^{re.escape(key)}\[([^]]+)\]=", content, re.MULTILINE
                    )
                ]
                if len(names) != len(set(names)):
                    errors.append(
                        f"{desktop.relative_to(ROOT)}: duplicate localized {key}"
                    )
                missing = missing_languages(set(names), languages)
                if missing:
                    errors.append(
                        f"{desktop.relative_to(ROOT)}: {key} missing "
                        f"{', '.join(missing)}"
                    )

    language_attribute = "{http://www.w3.org/XML/1998/namespace}lang"
    for policy in sorted(ROOT.glob("*/**/*.policy")):
        if "obj" in policy.parts:
            continue
        policy_count += 1
        try:
            document = ET.parse(policy)
        except ET.ParseError as error:
            errors.append(f"{policy.relative_to(ROOT)}: invalid XML: {error}")
            continue
        for action in document.findall(".//action"):
            for tag in ("description", "message"):
                names = [
                    node.attrib[language_attribute]
                    for node in action.findall(tag)
                    if language_attribute in node.attrib
                ]
                if len(names) != len(set(names)):
                    errors.append(
                        f"{policy.relative_to(ROOT)}: {action.get('id')} has "
                        f"duplicate {tag} locales"
                    )
                missing = missing_languages(set(names), languages)
                if missing:
                    errors.append(
                        f"{policy.relative_to(ROOT)}: {action.get('id')} {tag} "
                        f"missing {', '.join(missing)}"
                    )
    return desktop_count, policy_count


def verify() -> tuple[int, int, int, int]:
    languages = official_catalog_names()
    errors: list[str] = []
    package_count = 0
    catalog_count = 0
    for template in sorted(ROOT.glob("*/po/*.pot")):
        package_count += 1
        po_dir = template.parent
        catalogs = {path.stem: path for path in po_dir.glob("*.po")}
        for code, candidates in languages.items():
            if code == SOURCE_LANGUAGE:
                continue
            matches = [name for name in candidates if name in catalogs]
            if len(matches) != 1:
                errors.append(
                    f"{po_dir.relative_to(ROOT)}: {code} must have exactly one "
                    f"catalog named {' or '.join(candidates)}"
                )
                continue
            catalog = catalogs[matches[0]]
            catalog_count += 1
            formatted = run(
                "msgfmt",
                "--check",
                "--check-format",
                "--output-file=/dev/null",
                str(catalog),
            )
            if formatted.returncode:
                errors.append(f"{catalog.relative_to(ROOT)}: invalid gettext catalog")
                continue
            compared = run(
                "msgcmp",
                "--use-untranslated",
                "--no-fuzzy-matching",
                str(catalog),
                str(template),
            )
            if compared.returncode:
                errors.append(
                    f"{catalog.relative_to(ROOT)}: missing messages from "
                    f"{template.name}"
                )
            for selector, label in (
                ("--untranslated", "untranslated"),
                ("--only-fuzzy", "fuzzy"),
            ):
                selected = run(
                    "msgattrib",
                    selector,
                    "--no-obsolete",
                    str(catalog),
                )
                message_count = sum(
                    line.startswith("msgid ")
                    for line in selected.stdout.splitlines()
                )
                if selected.returncode or message_count > 1:
                    errors.append(
                        f"{catalog.relative_to(ROOT)}: contains {label} messages"
                    )
    desktop_count, policy_count = verify_inline_assets(languages, errors)
    if errors:
        raise RuntimeError("\n".join(errors))
    return package_count, catalog_count, desktop_count, policy_count


def main() -> int:
    try:
        packages, catalogs, desktops, policies = verify()
    except Exception as error:
        print(f"Localization policy failed:\n{error}", file=sys.stderr)
        return 1
    print(
        f"Localization policy passed: {packages} gettext packages, "
        f"{catalogs} official non-source catalogs, {desktops} desktop files, "
        f"and {policies} PolicyKit files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
