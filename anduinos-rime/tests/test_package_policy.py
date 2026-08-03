import re
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent


class RimePackagePolicyTests(unittest.TestCase):
    def test_package_is_the_canonical_self_contained_source(self):
        project = (PROJECT / "anduinos-rime.aosproj").read_text(encoding="utf-8")
        self.assertIn('IncludeFolder Include="assets/"', project)
        self.assertFalse((PROJECT / "download.sh").exists())

        required_assets = {
            "rime.lua",
            "rime_ice.dict.yaml",
            "rime_ice.schema.yaml",
            "melt_eng.dict.yaml",
            "melt_eng.schema.yaml",
            "cn_dicts/41448.dict.yaml",
            "cn_dicts/tencent.dict.yaml",
            "lua/convert_ar_num_to_zh.lua",
        }
        for relative_path in required_assets:
            with self.subTest(asset=relative_path):
                self.assertTrue((PROJECT / "assets" / relative_path).is_file())

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

    def test_package_installs_distribution_patch_in_shared_rime_data(self):
        project = (PROJECT / "anduinos-rime.aosproj").read_text(encoding="utf-8")
        self.assertIn('IncludeFolder Include="defaults/"', project)
        self.assertNotIn("/usr/share/anduinos-rime", project)

    def test_custom_defaults_select_rime_ice_without_version_pin(self):
        custom = (PROJECT / "defaults/anduinos_defaults.yaml").read_text(
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

    def test_schema_defaults_survive_a_global_user_override(self):
        custom = (PROJECT / "defaults/rime_ice.custom.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("anduinos_defaults:/patch/ascii_composer", custom)
        self.assertIn("anduinos_defaults:/patch/punctuator", custom)
        self.assertIn("anduinos_defaults:/patch/key_binder/bindings", custom)
        self.assertNotIn("schema_list", custom)

        schema = (PROJECT / "assets/rime_ice.schema.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("import_preset: default", schema)

        defaults = (PROJECT / "defaults/anduinos_defaults.yaml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(defaults.count("accept: Control+Shift+4"), 1)
        self.assertEqual(defaults.count("accept: Control+Shift+dollar"), 1)

    def test_global_entry_point_includes_canonical_defaults(self):
        custom = (PROJECT / "defaults/default.custom.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("__include: anduinos_defaults:/patch", custom)

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
