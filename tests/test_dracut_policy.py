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

    def test_desktop_bootstraps_existing_system_migration(self) -> None:
        project = ROOT / "anduinos-desktop/anduinos-desktop.aosproj"
        dependencies = {
            item.get("Include")
            for item in ET.parse(project).getroot().iter("Dependency")
        }
        self.assertIn("anduinos-dracut-migration", dependencies)

    def test_shared_apt_and_container_layers_do_not_pull_host_migration(self) -> None:
        for package in ("anduinos-apt-config", "anduinos-apt-config-dev"):
            with self.subTest(package=package):
                project = ROOT / package / f"{package}.aosproj"
                dependencies = {
                    item.get("Include")
                    for item in ET.parse(project).getroot().iter("Dependency")
                }
                self.assertNotIn("anduinos-dracut-migration", dependencies)

        container = ROOT / "anduinos-container/anduinos-container.aosproj"
        container_dependencies = {
            item.get("Include")
            for item in ET.parse(container).getroot().iter("Dependency")
        }
        self.assertNotIn("anduinos-dracut-migration", container_dependencies)

    def test_dracut_consumers_are_version_gated_on_the_guarded_core(self) -> None:
        expected = "anduinos-core-system (>= 2.0.2-3)"
        for package in (
            "anduinos-btrfs-snapshots-manager",
            "plymouth-anduinos",
        ):
            with self.subTest(package=package):
                project = ROOT / package / f"{package}.aosproj"
                dependencies = {
                    item.get("Include")
                    for item in ET.parse(project).getroot().iter("Dependency")
                }
                self.assertIn(expected, dependencies)

    def test_initrd_consumers_never_hide_generation_failures(self) -> None:
        for relative in (
            "anduinos-btrfs-snapshots-manager/scripts/postinst.sh",
            "anduinos-btrfs-snapshots-manager/scripts/postrm.sh",
            "plymouth-anduinos/scripts/postinst.sh",
            "plymouth-anduinos/scripts/prerm.sh",
        ):
            with self.subTest(script=relative):
                content = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("anduinos-dracut-verify --rebuild", content)
                self.assertNotRegex(
                    content,
                    r"dracut[^\n]*(?:\|\|\s*true|2>/dev/null)",
                )
        snapshots_postinst = (
            ROOT / "anduinos-btrfs-snapshots-manager/scripts/postinst.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("anduinos-dracut-verify --update-grub", snapshots_postinst)
        self.assertNotIn("/usr/sbin/update-grub", snapshots_postinst)

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
            if path.suffix.lower() == ".md":
                continue
            if path.name == "prebuild-check.sh":
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # Ubuntu's Dracut package deliberately retains the historical
            # executable/trigger name as its compatibility ABI. These files
            # divert and wrap the Dracut implementation; they never invoke the
            # removed initramfs-tools generator.
            compatibility_guard = {
                ROOT / "anduinos-core-system/anduinos-core-system.aosproj",
                ROOT / "anduinos-core-system/scripts/postinst.sh",
                ROOT / "anduinos-core-system/scripts/prerm.sh",
                ROOT / "anduinos-core-system/assets/anduinos-update-initramfs",
            }
            if path in compatibility_guard:
                content = content.replace("update-initramfs", "dracut-compat")
            if path == ROOT / "anduinos-core-system/scripts/preinst.sh":
                # Before unpacking the Dracut-only core, preinst must validate
                # the legacy image with the inspector that is still installed.
                # It never generates or updates an image through this ABI.
                content = content.replace("lsinitramfs", "legacy-inspector")
            if forbidden.search(content):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_migration_can_recover_after_the_legacy_generator_is_removed(self) -> None:
        service = (
            ROOT
            / "anduinos-dracut-migration/assets/anduinos-dracut-migration.service"
        ).read_text(encoding="utf-8")
        migrator = (
            ROOT
            / "anduinos-dracut-migration/assets/anduinos-dracut-migrate"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ConditionPathExists=/usr/sbin/update-initramfs", service)
        self.assertIn(
            "ConditionPathExists=!/var/lib/anduinos-dracut-migration/complete",
            service,
        )
        self.assertNotIn("update-initramfs", migrator)


if __name__ == "__main__":
    unittest.main()
