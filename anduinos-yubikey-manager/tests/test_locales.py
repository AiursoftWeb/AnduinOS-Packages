import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PO_DIR = ROOT / "po"
LOCALES = {
    "ar", "da", "de", "el", "en_GB", "en_US", "es", "fi", "fr", "hi",
    "id", "it", "ja", "ko", "nl", "pl", "pt", "pt_BR", "ro", "ru",
    "sv", "th", "tr", "uk", "vi", "zh_CN", "zh_HK", "zh_TW",
}
PLACEHOLDER = re.compile(r"\{\d+\}")
DESKTOP_LOCALES = LOCALES - {"en_GB", "en_US"}
RUST_I18N = re.compile(
    r'i18n\s*\(\s*"((?:[^"\\]|\\.)*)"\s*,?\s*\)',
    re.DOTALL,
)


def po_entries(path: Path):
    entries = []
    msgid = []
    msgstr = []
    active = None

    def finish():
        if msgid:
            entries.append(("".join(msgid), "".join(msgstr)))

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            finish()
            msgid = [ast.literal_eval(line[6:])]
            msgstr = []
            active = msgid
        elif line.startswith("msgstr "):
            msgstr = [ast.literal_eval(line[7:])]
            active = msgstr
        elif line.startswith('"') and active is not None:
            active.append(ast.literal_eval(line))
        elif not line:
            active = None
    finish()
    return entries


class LocaleCatalogTests(unittest.TestCase):
    def test_pot_exactly_matches_rust_i18n_literals(self):
        source_messages = set()
        for source in (ROOT / "src").glob("**/*.rs"):
            content = source.read_text(encoding="utf-8")
            for match in RUST_I18N.finditer(content):
                rust_literal = re.sub(r"\\\r?\n\s*", "", match.group(1))
                source_messages.add(ast.literal_eval(f'"{rust_literal}"'))
        pot_messages = {
            msgid
            for msgid, _ in po_entries(
                PO_DIR / "anduinos-yubikey-manager.pot"
            )
            if msgid
        }
        self.assertEqual(source_messages, pot_messages)

    def test_all_catalogs_exist_and_are_complete(self):
        self.assertEqual(LOCALES, {path.stem for path in PO_DIR.glob("*.po")})
        expected = {
            msgid
            for msgid, _ in po_entries(
                PO_DIR / "anduinos-yubikey-manager.pot"
            )
            if msgid
        }
        for locale in sorted(LOCALES):
            entries = po_entries(PO_DIR / f"{locale}.po")
            messages = [(msgid, msgstr) for msgid, msgstr in entries if msgid]
            self.assertEqual(expected, {msgid for msgid, _ in messages}, locale)
            self.assertFalse(
                [msgid for msgid, msgstr in messages if not msgstr],
                f"{locale} contains untranslated messages",
            )

    def test_numbered_placeholders_are_preserved(self):
        for locale in sorted(LOCALES):
            for msgid, msgstr in po_entries(PO_DIR / f"{locale}.po"):
                if not msgid:
                    continue
                self.assertEqual(
                    PLACEHOLDER.findall(msgid),
                    PLACEHOLDER.findall(msgstr),
                    f"{locale}: {msgid!r}",
                )

    def test_catalogs_do_not_contain_failed_llm_output(self):
        for locale in sorted(LOCALES):
            for msgid, msgstr in po_entries(PO_DIR / f"{locale}.po"):
                if not msgid:
                    continue
                self.assertNotIn("[Error]", msgstr, f"{locale}: {msgid!r}")
                self.assertFalse(
                    msgstr.startswith("\\") and not msgid.startswith("\\"),
                    f"{locale} has a spurious leading backslash: {msgid!r}",
                )

    def test_desktop_entry_covers_supported_locales(self):
        content = (
            ROOT / "data" / "com.anduinos.yubikeymanager.desktop"
        ).read_text(encoding="utf-8")
        for key in ("Name", "GenericName", "Comment", "Keywords"):
            found = set(
                re.findall(rf"^{key}\[([^\]]+)\]=", content, re.MULTILINE)
            )
            self.assertEqual(DESKTOP_LOCALES, found, key)
        for line in content.splitlines():
            if line.startswith("Keywords"):
                value = line.split("=", 1)[1]
                self.assertTrue(value.endswith(";"), line)
                self.assertNotIn("；", value, line)
                self.assertNotIn("؛", value, line)


if __name__ == "__main__":
    unittest.main()
