import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
UUID = "arcmenu@arcmenu.com"
DCONF_PATH = ROOT / "dconf/10-arcmenu.conf"


def dconf_value(key: str) -> str:
    source = DCONF_PATH.read_text(encoding="utf-8")
    matches = re.findall(
        rf"^{re.escape(key)}\s*=\s*([^\n]+?)\s*$", source, re.MULTILINE
    )
    if len(matches) != 1:
        raise AssertionError(
            f"dconf/10-arcmenu.conf must contain exactly one {key} assignment"
        )
    return matches[0]


def packaged_suites() -> tuple[str, ...]:
    project = ET.parse(ROOT / "gnome-shell-extension-arcmenu.aosproj")
    target_suites = project.findtext(".//TargetSuites")
    if not target_suites:
        raise AssertionError("The package does not declare TargetSuites")
    return tuple(suite.removesuffix("-addon") for suite in target_suites.split())


class UpdateNotifierTests(unittest.TestCase):
    def test_packaged_metadata_versions_match_dconf_default(self):
        configured_version = dconf_value("update-notifier-project-version")
        self.assertRegex(configured_version, r"^\d+$")
        expected = int(configured_version)

        observed = {}
        for suite in packaged_suites():
            metadata_path = ROOT / "deploy" / suite / UUID / "metadata.json"
            self.assertTrue(
                metadata_path.is_file(),
                f"{suite}: generated metadata is missing; run download.sh first",
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            version = metadata.get("version")
            self.assertIsInstance(
                version, int, f"{suite}: metadata version must be an integer"
            )
            observed[suite] = version
            self.assertEqual(
                version,
                expected,
                f"{suite}: packaged ArcMenu version {version} does not match "
                f"dconf update-notifier-project-version={expected}; users would "
                "receive an unexpected ArcMenu update notification",
            )
        self.assertTrue(observed)

    def test_upstream_update_notifications_are_disabled(self):
        self.assertEqual(
            dconf_value("update-notifier-enabled"),
            "false",
            "AnduinOS must disable ArcMenu's upstream release notifications",
        )

    def test_obsolete_notification_setting_is_not_shipped(self):
        source = DCONF_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(source, r"^show-update-notification-v64\s*=",)


if __name__ == "__main__":
    unittest.main()
