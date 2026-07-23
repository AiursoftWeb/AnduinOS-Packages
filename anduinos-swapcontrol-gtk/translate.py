#!/usr/bin/env python3
"""Translate untranslated .po entries using the aiursoft-localization API."""

import re
import os
import sys
import json
import time
import urllib.request
from pathlib import Path

API_URL = "https://ollama.aiursoft.com/v1/chat/completions"
API_KEY = "a7e939b2a0cc415baa8fd9d05fdaf091"
MODEL = "aiursoft-localization:latest"
BATCH_SIZE = 15  # translations per API call

# Locale code → language name (for the prompt)
LANG_NAMES = {
    "ar_SA": "Arabic",
    "da_DK": "Danish",
    "de_DE": "German",
    "el_GR": "Greek",
    "en_GB": "English (UK)",
    "en_US": "English (US)",
    "es_ES": "Spanish",
    "fi_FI": "Finnish",
    "fr_FR": "French",
    "hi_IN": "Hindi",
    "id_ID": "Indonesian",
    "it_IT": "Italian",
    "ja_JP": "Japanese",
    "ko_KR": "Korean",
    "nl_NL": "Dutch",
    "pl_PL": "Polish",
    "pt_BR": "Portuguese (Brazil)",
    "pt_PT": "Portuguese (Portugal)",
    "ro_RO": "Romanian",
    "ru_RU": "Russian",
    "sv_SE": "Swedish",
    "th_TH": "Thai",
    "tr_TR": "Turkish",
    "uk_UA": "Ukrainian",
    "vi_VN": "Vietnamese",
    "zh_CN": "Chinese (Simplified)",
    "zh_HK": "Chinese (Hong Kong)",
    "zh_TW": "Chinese (Traditional)",
}


def parse_po(path: str) -> list[dict]:
    """Parse a .po file into a list of entry dicts."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    entries = []
    blocks = re.split(r"\n\n+", content.strip())

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        msgid_match = re.search(r'msgid\s+"((?:[^"\\]|\\.)*)"', block)
        if msgid_match is None:
            # Header block (msgid "")
            entries.append({"header": True, "raw": block})
            continue

        msgid = msgid_match.group(1)
        # Unescape
        msgid = msgid.replace('\\"', '"').replace("\\n", "\n")

        msgstr_match = re.search(r'msgstr\s+"((?:[^"\\]|\\.)*)"', block)
        msgstr = ""
        if msgstr_match:
            msgstr = msgstr_match.group(1)
            msgstr = msgstr.replace('\\"', '"').replace("\\n", "\n")

        # Check for multi-line msgstr
        if not msgstr_match:
            # Check for empty msgstr ""
            if re.search(r'msgstr\s+""', block):
                msgstr = ""

        entries.append({
            "header": False,
            "raw": block,
            "msgid": msgid,
            "msgstr": msgstr,
        })

    return entries


def write_po(path: str, entries: list[dict]):
    """Write entries back to a .po file."""
    with open(path, "w", encoding="utf-8") as f:
        for i, entry in enumerate(entries):
            if entry.get("header"):
                f.write(entry["raw"] + "\n\n")
            else:
                msgid = entry["msgid"].replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                msgstr = entry["msgstr"].replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                f.write(f'msgid "{msgid}"\nmsgstr "{msgstr}"\n')
                if i < len(entries) - 1:
                    f.write("\n")


def call_api(prompt: str) -> str:
    """Call the localization API and return the response text."""
    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
                return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"    API error (attempt {attempt+1}): {e}")
            time.sleep(2)
    raise RuntimeError("API call failed after 3 attempts")


def translate_batch(lang_name: str, texts: list[str]) -> list[str]:
    """Translate a batch of English texts to the target language."""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

    prompt = (
        f"Translate the following English strings to {lang_name}.\n"
        f"Rules:\n"
        f"- CRITICAL: Translate 'Swap' as 'Virtual Memory' (the consumer term Windows users know). "
        f"Never use technical jargon like 'exchange space', 'swap space', 'paging', or direct transliterations. "
        f"Use the standard {lang_name} term for 'Virtual Memory'.\n"
        f"- 'Disk Swap' means 'Disk Virtual Memory'.\n"
        f"- 'Swap file' means 'Virtual Memory file'.\n"
        f"- 'swappiness' is a technical kernel parameter — translate it naturally.\n"
        f"- Keep any <i>...</i> markup, format specifiers like {{}}, {{:.0}}, {{:.1}} unchanged.\n"
        f"- Preserve HTML-like tags and technical terms like 'Zram', 'Zswap' unchanged.\n"
        f"- Respond with EXACTLY {len(texts)} lines.\n"
        f"- Each line must be the translation of the corresponding numbered item.\n"
        f"- Do NOT include numbers, quotes, or any extra text.\n"
        f"- Just the translations, one per line.\n\n"
        f"{numbered}"
    )

    response = call_api(prompt)
    lines = [line.strip() for line in response.strip().split("\n") if line.strip()]
    # Remove leading numbers if present (e.g., "1. translation")
    cleaned = []
    for line in lines:
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        cleaned.append(line)

    # Pad or truncate to match
    while len(cleaned) < len(texts):
        cleaned.append(texts[len(cleaned)])  # fallback to English
    return cleaned[:len(texts)]


def translate_po(po_path: str, lang_code: str):
    """Translate all untranslated entries in a .po file."""
    lang_name = LANG_NAMES.get(lang_code, lang_code)
    entries = parse_po(po_path)

    # Find untranslated entries
    untranslated = []
    for entry in entries:
        if not entry.get("header") and entry["msgstr"] == "" and entry["msgid"] != "":
            untranslated.append(entry)

    if not untranslated:
        print(f"  ✅ All translated — nothing to do")
        return

    print(f"  📝 {len(untranslated)} untranslated, translating in batches of {BATCH_SIZE}...")

    total = len(untranslated)
    for i in range(0, total, BATCH_SIZE):
        batch = untranslated[i : i + BATCH_SIZE]
        texts = [e["msgid"] for e in batch]

        try:
            translations = translate_batch(lang_name, texts)
        except Exception as e:
            print(f"    ❌ Batch {i//BATCH_SIZE + 1} failed: {e}")
            continue

        for entry, translation in zip(batch, translations):
            entry["msgstr"] = translation

        print(f"    ✓ {min(i+BATCH_SIZE, total)}/{total}")
        time.sleep(0.5)  # rate limit

    write_po(po_path, entries)
    print(f"  💾 Saved {po_path}")


def main():
    po_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "po")
    if not os.path.isdir(po_dir):
        # Try relative to cwd
        po_dir = "po"
    po_dir = Path(po_dir)

    # Get list of languages to translate (skip English)
    targets = []
    for po_file in sorted(po_dir.glob("*.po")):
        lang = po_file.stem
        if lang == "en":
            continue  # skip source language
        targets.append((str(po_file), lang))

    print(f"Translating {len(targets)} languages...")
    print(f"API: {MODEL}\n")

    for po_path, lang_code in targets:
        print(f"🌐 {lang_code} ({LANG_NAMES.get(lang_code, '?')}):")
        try:
            translate_po(po_path, lang_code)
        except Exception as e:
            print(f"  ❌ Failed: {e}")
        print()

    print("Done! Run compile-locales.sh to regenerate .mo files.")


if __name__ == "__main__":
    main()
