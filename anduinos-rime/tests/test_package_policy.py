import re
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent


class RimePackagePolicyTests(unittest.TestCase):
    def test_package_does_not_depend_on_or_replace_language_selector(self):
        project = (PROJECT / "anduinos-rime.aosproj").read_text(encoding="utf-8")
        self.assertNotIn("language-selector-common", project)
        self.assertNotIn("pkg_depends", project)
        self.assertFalse((PROJECT / "assets/pkg_depends").exists())

    def test_package_does_not_own_upstream_default_yaml(self):
        project = (PROJECT / "anduinos-rime.aosproj").read_text(encoding="utf-8")
        self.assertNotIn('Target="/usr/share/rime-data/default.yaml"', project)
        self.assertFalse((PROJECT / "assets/default.yaml").exists())
        self.assertTrue((PROJECT / "defaults/default.custom.yaml").is_file())

    def test_custom_defaults_select_rime_ice_without_version_pin(self):
        custom = (PROJECT / "defaults/default.custom.yaml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(custom, r"(?m)^patch:\s*$")
        self.assertRegex(custom, r"(?m)^\s+schema_list:\s*$")
        self.assertRegex(custom, r"(?m)^\s+- schema: rime_ice\s*$")
        self.assertNotRegex(custom, r"(?m)^\s*config_version:")
        self.assertEqual(
            set(re.findall(r"(?m)^  ([a-z_][a-z0-9_]*):\s*$", custom)),
            {
                "schema_list",
                "menu",
                "switcher",
                "ascii_composer",
                "navigator",
                "punctuator",
                "recognizer",
                "key_binder",
            },
        )

    def test_migration_only_removes_historical_diversions(self):
        postinst = (PROJECT / "scripts/postinst.sh").read_text(encoding="utf-8")
        self.assertNotIn("--add", postinst)
        self.assertEqual(postinst.count("--remove --rename"), 1)
        self.assertIn("/usr/share/rime-data/default.yaml.prelude", postinst)
        self.assertIn(
            "/usr/share/language-selector/data/pkg_depends.ubuntu", postinst
        )


if __name__ == "__main__":
    unittest.main()
