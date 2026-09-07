#!/usr/bin/env python3
"""Package-local boot policy checks."""
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
    def test_own_package_does_not_pull_legacy_stack(self) -> None:
        violations: list[str] = []
        for project in sorted(ROOT.glob("*.aosproj")):
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
        project = ROOT / "anduinos-core-system.aosproj"
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
                ROOT / "anduinos-core-system.aosproj",
                ROOT / "scripts/postinst.sh",
                ROOT / "scripts/prerm.sh",
                ROOT / "assets/anduinos-update-initramfs",
            }
            if path in compatibility_guard:
                content = content.replace("update-initramfs", "dracut-compat")
            if path == ROOT / "scripts/preinst.sh":
                # Before unpacking the Dracut-only core, preinst must validate
                # the legacy image with the inspector that is still installed.
                # It never generates or updates an image through this ABI.
                content = content.replace("lsinitramfs", "legacy-inspector")
            if forbidden.search(content):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_core_system_owns_complete_architecture_boot_stacks(self):
        core = ET.parse(ROOT / "anduinos-core-system.aosproj").getroot()
        dependencies = {
            (item.get("Include"), item.get("Condition"))
            for item in core.iter("Dependency")
        }

        amd64 = "'$(Arch)' == 'amd64'"
        arm64 = "'$(Arch)' == 'arm64'"
        self.assertTrue(
            {
                ("grub-pc-bin", amd64),
                ("grub-efi-amd64-bin", amd64),
                ("grub-efi-amd64-signed", amd64),
                ("grub-efi-arm64-bin", arm64),
                ("grub-efi-arm64-signed", arm64),
                ("shim-signed", None),
            }
            <= dependencies
        )

    def test_no_security_preset_is_installed(self):
        self.assertFalse((ROOT / "assets/20-anduinos-security.preset").exists())
        project = ET.parse(ROOT / "anduinos-core-system.aosproj").getroot()
        self.assertFalse(any(item.get("Target") == "/usr/lib/systemd/system-preset/20-anduinos-security.preset" for item in project.iter("IncludeFile")))


if __name__ == "__main__":
    unittest.main()
