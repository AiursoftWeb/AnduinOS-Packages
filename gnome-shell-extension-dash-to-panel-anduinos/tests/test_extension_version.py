import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
UUID = "dash-to-panel@jderose9.github.com"


def configured_extension_version() -> int:
    source = (ROOT / "dconf/14-dash-to-panel.conf").read_text(encoding="utf-8")
    matches = re.findall(r"^extension-version\s*=\s*(\d+)\s*$", source, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(
            "dconf/14-dash-to-panel.conf must contain exactly one integer "
            "extension-version"
        )
    return int(matches[0])


def packaged_suites() -> tuple[str, ...]:
    project = ET.parse(
        ROOT / "gnome-shell-extension-dash-to-panel-anduinos.aosproj"
    )
    target_suites = project.findtext(".//TargetSuites")
    if not target_suites:
        raise AssertionError("The package does not declare TargetSuites")
    return tuple(suite.removesuffix("-addon") for suite in target_suites.split())


class ExtensionVersionTests(unittest.TestCase):
    def test_packaged_metadata_versions_match_dconf_default(self):
        expected = configured_extension_version()
        observed = {}
        for suite in packaged_suites():
            metadata_path = ROOT / "deploy" / suite / UUID / "metadata.json"
            self.assertTrue(
                metadata_path.is_file(),
                f"{suite}: generated metadata is missing; run download.sh first",
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            version = metadata.get("version")
            self.assertIsInstance(version, int, f"{suite}: metadata version must be an integer")
            observed[suite] = version
            self.assertEqual(
                version,
                expected,
                f"{suite}: packaged Dash to Panel version {version} does not match "
                f"dconf extension-version={expected}",
            )
        self.assertTrue(observed)


if __name__ == "__main__":
    unittest.main()
