import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('localization_policy', Path(__file__).parents[1] / 'verify-localizations.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class DesktopSectionTests(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'example'
            (package / 'po').mkdir(parents=True)
            (package / 'po/example.pot').touch()
            (package / 'example.desktop').write_text(text)
            errors = []
            with patch.object(policy, 'ROOT', root):
                policy.verify_inline_assets({'en_US': ('en_US',), 'zh_CN': ('zh_CN',)}, errors)
            return errors

    def test_same_name_in_distinct_groups_is_valid(self):
        self.assertEqual(self.check('[Desktop Entry]\nName[zh_CN]=名称\n[X-AppStream-Metadata]\nName[zh_CN]=名称\n'), [])

    def test_duplicate_in_same_group_is_rejected(self):
        errors = self.check('[Desktop Entry]\nName[zh_CN]=甲\nName[zh_CN]=乙\n')
        self.assertTrue(any('duplicate localized Name' in e for e in errors))

    def test_other_group_cannot_fill_missing_translation(self):
        errors = self.check('[Desktop Entry]\nName[en_GB]=Name\n[X-AppStream-Metadata]\nName[zh_CN]=名称\n')
        self.assertTrue(any('[Desktop Entry]: Name missing zh_CN' in e for e in errors))
