#!/usr/bin/env python3
"""Repository-wide release gate for the single-generator boot policy."""

from __future__ import annotations

from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PACKAGES = {
    "casper",
    "initramfs-tools",
    "initramfs-tools-core",
    "initramfs-tools-bin",
    "busybox-initramfs",
    "finalrd",
    "live-tools",
}


def package_names(value: str) -> set[str]:
    return {
        alternative.strip().split()[0]
        for group in value.split(",")
        for alternative in group.split("|")
        if alternative.strip()
    }


class PureDracutPolicyTests(unittest.TestCase):
    def test_no_package_can_depend_on_or_recommend_the_legacy_stack(self) -> None:
        violations: list[str] = []
        for project in sorted(ROOT.glob("*/*.aosproj")):
            tree = ET.parse(project)
            for tag in ("Dependency", "Recommend"):
                for item in tree.iter(tag):
                    names = package_names(item.get("Include", ""))
                    forbidden = sorted(names & FORBIDDEN_PACKAGES)
                    if forbidden:
                        violations.append(
                            f"{project.relative_to(ROOT)}:{tag}:{','.join(forbidden)}"
                        )
        self.assertEqual(violations, [])

    def test_core_system_hard_requires_dracut_and_conflicts_old_stack(self) -> None:
        project = ROOT / "anduinos-core-system/anduinos-core-system.aosproj"
        root = ET.parse(project).getroot()
        dependencies = {
            item.get("Include") for item in root.iter("Dependency")
        }
        self.assertTrue(
            {"dracut", "dracut-core", "dracut-install"} <= dependencies
        )
        conflicts = package_names(root.findtext(".//Conflicts", ""))
        self.assertTrue(
            {
                "casper",
                "initramfs-tools",
                "initramfs-tools-core",
                "initramfs-tools-bin",
                "busybox-initramfs",
                "finalrd",
            }
            <= conflicts
        )

    def test_production_tree_has_no_legacy_generator_abi(self) -> None:
        forbidden = re.compile(
            r"(/usr/share/initramfs-tools|/etc/initramfs-tools|"
            r"\b(?:mkinitramfs|lsinitramfs|update-initramfs)\b|"
            r"boot=casper|/casper/)"
        )
        violations: list[str] = []
        ignored_parts = {
            ".git",
            "bin",
            "obj",
            "target",
            "tests",
            "docs",
            "specs",
        }
        for path in ROOT.rglob("*"):
            if not path.is_file() or ignored_parts & set(path.parts):
                continue
            if path.name == "prebuild-check.sh":
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            migration_condition = (
                ROOT
                / "anduinos-dracut-migration/assets/anduinos-dracut-migration.service"
            )
            if path == migration_condition:
                content = content.replace(
                    "ConditionPathExists=/usr/sbin/update-initramfs", ""
                )
            if forbidden.search(content):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_migration_only_detects_and_never_executes_the_legacy_generator(self) -> None:
        service = (
            ROOT
            / "anduinos-dracut-migration/assets/anduinos-dracut-migration.service"
        ).read_text(encoding="utf-8")
        migrator = (
            ROOT
            / "anduinos-dracut-migration/assets/anduinos-dracut-migrate"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            service.count("ConditionPathExists=/usr/sbin/update-initramfs"),
            1,
        )
        self.assertNotIn("update-initramfs", migrator)


if __name__ == "__main__":
    unittest.main()
