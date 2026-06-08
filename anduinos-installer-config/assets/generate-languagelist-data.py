#!/usr/bin/env python3
"""
Generate ubiquity-languagelist.data from ubiquity-languagelist.

WHY THIS SCRIPT EXISTS
======================
Ubiquity reads the binary .data file to render the language list in the
installer UI — NOT the text languagelist.  If you add, remove, or reorder
languages in ubiquity-languagelist without regenerating the .data file,
the UI will silently show a stale list.  (This already happened once:
6 languages were missing from the UI because the .data file fell out of
sync with the text file.)

WHEN TO RUN
===========
Run this script every time ubiquity-languagelist is modified.  It is NOT
run automatically during the package build, so you must run it by hand
before committing.

HOW TO RUN
==========
    cd AnduinOS-Packages/anduinos-installer-config/assets
    python3 generate-languagelist-data.py

Then commit BOTH files together:
    ubiquity-languagelist          (the text source of truth)
    ubiquity-languagelist.data     (the generated binary)

WHAT TO DO WHEN ADDING A NEW LANGUAGE
=====================================
1. Add a line to ubiquity-languagelist with the locale code and encoding.
2. Add an entry to the LANG_DATA dict below with the English name and
   native name.
3. Run:  python3 generate-languagelist-data.py
4. Commit both files.

FILE FORMATS
============
Text languagelist (semicolon-delimited):
    <lang_code>;<encoding>;<country>;<locale>;<fallback_locales>;<console>

.data file (colon-delimited, one line per language, sorted by English name):
    <encoding>:<lang_code>:<English name>:<native name>
"""

import sys
import os

# Complete mapping of language code → (English name, native name).
# Encoding numbers come from the text languagelist (column 2).
#
# Encoding legend:
#   0 – Latin (English)
#   1 – Latin-derived (most European languages)
#   2 – Cyrillic / Greek
#   3 – CJK / Indic / Thai / other complex scripts
LANG_DATA = {
    "ar":  ("Arabic",                "العربية"),
    "da":  ("Danish",                "Dansk"),
    "de":  ("German",                "Deutsch"),
    "el":  ("Greek",                 "Ελληνικά"),
    "en":  ("English",               "English"),
    "en_GB": ("English (United Kingdom)", "English (United Kingdom)"),
    "es":  ("Spanish",               "Español"),
    "fi":  ("Finnish",               "Suomi"),
    "fr":  ("French",                "Français"),
    "hi":  ("Hindi",                 "हिन्दी"),
    "id":  ("Indonesian",            "Bahasa Indonesia"),
    "it":  ("Italian",               "Italiano"),
    "ja":  ("Japanese",              "日本語"),
    "ko":  ("Korean",                "한국어"),
    "nl":  ("Dutch",                 "Nederlands"),
    "pl":  ("Polish",                "Polski"),
    "pt":  ("Portuguese",            "Português"),
    "pt_BR": ("Portuguese (Brazil)", "Português do Brasil"),
    "ro":  ("Romanian",              "Română"),
    "ru":  ("Russian",               "Русский"),
    "sv":  ("Swedish",               "Svenska"),
    "th":  ("Thai",                  "ภาษาไทย"),
    "tr":  ("Turkish",               "Türkçe"),
    "uk":  ("Ukrainian",             "Українська"),
    "vi":  ("Vietnamese",            "Tiếng Việt"),
    "zh_CN": ("Chinese (Simplified)",  "中文(简体)"),
    "zh_HK": ("Chinese (Hong Kong)",   "中文 (香港)"),
    "zh_TW": ("Chinese (Traditional)", "中文(繁體)"),
}


def parse_languagelist(path):
    """Parse the text languagelist, returning {lang_code: encoding}."""
    encodings = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            code = parts[0]
            enc = parts[1]
            encodings[code] = enc
    return encodings


def generate_data(encodings, output_path):
    """Generate the .data file from parsed encodings and LANG_DATA."""
    entries = []

    # Two system pseudo-locales always come first in the .data file.
    entries.append(("0", "C", "C", "No localization (ASCII)"))
    entries.append(("0", "C.UTF-8", "C.UTF-8", "No localization (UTF-8)"))

    for code, enc in encodings.items():
        if code in LANG_DATA:
            en_name, native_name = LANG_DATA[code]
            entries.append((enc, code, en_name, native_name))
        else:
            print(
                f"ERROR: '{code}' is in the text languagelist but not in LANG_DATA. "
                f"Add it to LANG_DATA in this script, then re-run.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Keep system entries at the top; sort the rest by English name.
    system_entries = entries[:2]
    lang_entries = sorted(entries[2:], key=lambda e: e[2].lower())
    entries = system_entries + lang_entries

    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        for enc, code, en_name, native_name in entries:
            f.write(f"{enc}:{code}:{en_name}:{native_name}\n")

    print(f"Wrote {len(entries)} entries to {output_path}")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(script_dir, "ubiquity-languagelist")
    output_path = os.path.join(script_dir, "ubiquity-languagelist.data")

    encodings = parse_languagelist(input_path)
    generate_data(encodings, output_path)


if __name__ == "__main__":
    main()
