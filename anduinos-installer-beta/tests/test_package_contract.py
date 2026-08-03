import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_package_verifier():
    path = ROOT / "scripts/verify-built-package.py"
    spec = importlib.util.spec_from_file_location(
        "built_package_verifier", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageContractTests(unittest.TestCase):
    def test_appstream_publishes_the_live_installer_as_an_application(self):
        root = ET.parse(ROOT / "anduinos-installer-beta.aosproj").getroot()
        application = root.find(".//AppStreamApplication")
        self.assertIsNotNone(application)
        self.assertEqual(
            application.get("Include"),
            "assets/anduinos-installer-beta.desktop",
        )
        self.assertEqual(
            application.get("Icon"),
            "assets/anduinos-installer-beta.svg",
        )
        screenshots = root.findall(".//AppStreamScreenshot")
        self.assertEqual(
            {screenshot.get("Include") for screenshot in screenshots},
            {"screenshots/storage.png", "screenshots/welcome.png"},
        )
        self.assertEqual(
            [
                screenshot.get("Include")
                for screenshot in screenshots
                if screenshot.get("Default") == "true"
            ],
            ["screenshots/storage.png"],
        )
        for screenshot in screenshots:
            self.assertTrue((ROOT / screenshot.get("Include")).is_file())

    def test_manifest_installs_the_source_tree_and_runtime_dependencies(self):
        root = ET.parse(ROOT / "anduinos-installer-beta.aosproj").getroot()
        folders = {
            (item.get("Include"), item.get("Target"))
            for item in root.iter("IncludeFolder")
        }
        dependencies = {
            item.get("Include") for item in root.iter("Dependency")
        }
        self.assertIn(
            ("src/", "/usr/lib/anduinos-installer-beta/"),
            folders,
        )
        self.assertIn(
            ("assets/icons/", "/usr/share/anduinos-installer-beta/icons/"),
            folders,
        )
        files = {
            (item.get("Include"), item.get("Target"))
            for item in root.iter("IncludeFile")
        }
        self.assertIn(
            (
                "assets/style.css",
                "/usr/share/anduinos-installer-beta/style.css",
            ),
            files,
        )
        self.assertIn(
            (
                "data/languages.json",
                "/usr/share/anduinos-installer-beta/languages.json",
            ),
            files,
        )
        self.assertTrue(
            {
                "python3",
                "python3-unidecode",
                "parted",
                "dosfstools",
                "efibootmgr",
                "gnome-control-center",
                "network-manager",
                "util-linux",
                "polkitd",
            }
            <= dependencies
        )
        self.assertIn(
            (
                "assets/anduinos-installer-storage-probe",
                "/usr/bin/anduinos-installer-storage-probe",
            ),
            files,
        )
        self.assertIn(
            (
                "assets/com.anduinos.installer-beta.policy",
                "/usr/share/polkit-1/actions/com.anduinos.installer-beta.policy",
            ),
            files,
        )

    def test_internal_vm_clis_load_but_have_no_public_launcher(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        for name in (
            "guided_test_plan_cli.py",
            "guided_test_evidence_cli.py",
        ):
            with self.subTest(name=name):
                result = subprocess.run(
                    (sys.executable, str(ROOT / "src" / name), "--help"),
                    capture_output=True,
                    text=True,
                    env=environment,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        launcher = ROOT / "assets/anduinos-installer-executor"
        result = subprocess.run(
            ("/bin/sh", str(launcher), "--guided-destructive-test"),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not accept arguments", result.stderr)

    def test_built_package_verifier_enforces_the_private_tool_contract(self):
        verifier = load_package_verifier()
        self.assertEqual(
            verifier.parse_dependencies(
                "python3 (>= 3.12), parted, util-linux:any"
            ),
            {"python3", "parted", "util-linux"},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in verifier.REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if relative == Path("usr/bin/anduinos-installer-executor"):
                    path.write_text(
                        '#!/bin/sh\nif [ "$#" -ne 0 ]; then exit 2; fi\n'
                    )
                    path.chmod(0o755)
                elif relative == Path(
                    "usr/bin/anduinos-installer-storage-probe"
                ):
                    path.write_text(
                        '#!/bin/sh\nif [ "$#" -ne 1 ]; then exit 2; fi\n'
                    )
                    path.chmod(0o755)
                else:
                    path.write_text("# package fixture\n")
            result = verifier.verify_staged_root(root)
            self.assertEqual(
                result["required_files"], len(verifier.REQUIRED_FILES)
            )
            (root / verifier.REQUIRED_FILES[0]).unlink()
            with self.assertRaisesRegex(RuntimeError, "missing"):
                verifier.verify_staged_root(root)


if __name__ == "__main__":
    unittest.main()
