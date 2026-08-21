#!/usr/bin/env python3
"""Verify that every Rust gettext call is catalogued and translated."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOTS = (
    ROOT / "src" / "btrfs-snapshots-manager" / "src",
    ROOT / "src" / "btrfs-snapshots-manager-notifier" / "src",
)
PYTHON_SOURCES = (ROOT / "data" / "anduinos_btrfs_snapshots_manager_file_history.py",)
POT = ROOT / "po" / "anduinos-btrfs-snapshots-manager.pot"
ZH_CN = ROOT / "po" / "zh_CN.po"
CALL = re.compile(r"\btrf?\(\s*(\"(?:\\.|[^\"\\])*\")", re.DOTALL)
PYTHON_CALL = re.compile(r"\b_\(\s*(\"(?:\\.|[^\"\\])*\")", re.DOTALL)
RAW_GTK_CALL = re.compile(
    r"\b(?:set_title|set_subtitle|set_description|set_tooltip_text|"
    r"set_placeholder_text|set_heading|set_body|with_label|title|subtitle|"
    r"description|tooltip_text|placeholder_text)\s*\(\s*(?:Some\s*\(\s*)?"
    r"(\"(?:\\.|[^\"\\])*\")",
    re.DOTALL,
)
RAW_GTK_CONSTRUCTOR = re.compile(
    r"\b(?:Label::new\(\s*Some|WindowTitle::new)\s*\(\s*"
    r"(\"(?:\\.|[^\"\\])*\")",
    re.DOTALL,
)
RAW_RESPONSE_LABEL = re.compile(
    r"\badd_response\(\s*\"(?:\\.|[^\"\\])*\"\s*,\s*"
    r"(\"(?:\\.|[^\"\\])*\")",
    re.DOTALL,
)
NON_LANGUAGE_LITERALS = {"", ":", "/", "+"}


def rust_messages() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for source_root in SOURCE_ROOTS:
        for source in sorted(source_root.rglob("*.rs")):
            text = source.read_text(encoding="utf-8")
            for match in CALL.finditer(text):
                try:
                    message = ast.literal_eval(match.group(1))
                except (SyntaxError, ValueError) as error:
                    raise RuntimeError(
                        f"cannot parse gettext literal in {source}: {error}"
                    ) from error
                found.setdefault(message, set()).add(str(source.relative_to(ROOT)))
    return found


def python_messages() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for source in PYTHON_SOURCES:
        text = source.read_text(encoding="utf-8")
        for match in PYTHON_CALL.finditer(text):
            message = ast.literal_eval(match.group(1))
            found.setdefault(message, set()).add(str(source.relative_to(ROOT)))
    return found


def raw_gtk_messages() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for source in sorted(SOURCE_ROOTS[0].rglob("*.rs")):
        text = source.read_text(encoding="utf-8")
        for pattern in (RAW_GTK_CALL, RAW_GTK_CONSTRUCTOR, RAW_RESPONSE_LABEL):
            for match in pattern.finditer(text):
                try:
                    message = ast.literal_eval(match.group(1))
                except (SyntaxError, ValueError) as error:
                    raise RuntimeError(
                        f"cannot parse GTK string literal in {source}: {error}"
                    ) from error
                if message not in NON_LANGUAGE_LITERALS:
                    found.setdefault(message, set()).add(str(source.relative_to(ROOT)))
    return found


def po_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    current_id: list[str] | None = None
    current_value: list[str] | None = None
    active: list[str] | None = None

    def finish() -> None:
        nonlocal current_id, current_value, active
        if current_id is not None and current_value is not None:
            message_id = "".join(current_id)
            if message_id:
                entries[message_id] = "".join(current_value)
        current_id = None
        current_value = None
        active = None

    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if line.startswith("msgid "):
            finish()
            current_id = [ast.literal_eval(line[6:])]
            current_value = []
            active = current_id
        elif line.startswith("msgstr ") and current_id is not None:
            current_value = [ast.literal_eval(line[7:])]
            active = current_value
        elif line.startswith('"') and active is not None:
            try:
                active.append(ast.literal_eval(line))
            except (SyntaxError, ValueError) as error:
                raise RuntimeError(f"cannot parse {path}:{number}: {error}") from error
        elif not line:
            finish()
    finish()
    return entries


def main() -> int:
    source_messages = rust_messages()
    for message, locations in python_messages().items():
        source_messages.setdefault(message, set()).update(locations)
    untranslated_gtk = raw_gtk_messages()
    template = po_entries(POT)
    chinese = po_entries(ZH_CN)

    missing_template = sorted(set(source_messages) - set(template))
    missing_chinese = sorted(set(source_messages) - set(chinese))
    empty_chinese = sorted(
        message for message in source_messages if message in chinese and not chinese[message]
    )

    failed = False
    for message, locations in sorted(untranslated_gtk.items()):
        location_text = ", ".join(sorted(locations))
        print(
            f"untranslated GTK literal: {message!r} ({location_text})",
            file=sys.stderr,
        )
        failed = True
    for label, messages in (
        ("missing from POT", missing_template),
        ("missing from zh_CN.po", missing_chinese),
        ("empty in zh_CN.po", empty_chinese),
    ):
        for message in messages:
            locations = ", ".join(sorted(source_messages[message]))
            print(f"{label}: {message!r} ({locations})", file=sys.stderr)
            failed = True

    if failed:
        return 1
    print(
        f"i18n coverage verified: {len(source_messages)} source messages, "
        "all present in POT and zh_CN.po"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
