#!/usr/bin/env python3
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "anduinos-apt-config/assets/52anduinos-unattended-upgrades"
PROJECTS = (
    ROOT / "anduinos-apt-config/anduinos-apt-config.aosproj",
    ROOT / "anduinos-apt-config-dev/anduinos-apt-config-dev.aosproj",
)


class UnattendedUpgradesPolicyTests(unittest.TestCase):
    def test_only_ubuntu_base_and_security_are_automatic(self) -> None:
        policy = POLICY.read_text(encoding="utf-8")
        self.assertIn('"o=Ubuntu,a=${distro_codename}";', policy)
        self.assertIn('"o=Ubuntu,a=${distro_codename}-security";', policy)
        self.assertNotIn("-updates", policy)
        self.assertNotIn("Aiursoft", policy)
        self.assertNotIn("AnduinOS", policy)

    def test_production_and_development_sources_ship_the_same_policy(self) -> None:
        for project in PROJECTS:
            with self.subTest(project=project.name):
                source = project.read_text(encoding="utf-8")
                self.assertIn("52anduinos-unattended-upgrades", source)
                self.assertIn("test_unattended_upgrades_policy.py", source)


if __name__ == "__main__":
    unittest.main()
