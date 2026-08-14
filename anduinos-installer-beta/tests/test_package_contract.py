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
    def test_core_system_owns_complete_architecture_boot_stacks(self):
        package_root = ROOT.parent
        core = ET.parse(
            package_root
            / "anduinos-core-system"
            / "anduinos-core-system.aosproj"
        ).getroot()
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

    def test_passwordless_sudo_contract_matches_security_center(self):
        package_root = ROOT.parent
        installer = (
            ROOT / "src/installer_core/system_config.py"
        ).read_text(encoding="utf-8")
        helper = (
            package_root / "anduinos-yubikey-manager/data/helper"
        ).read_text(encoding="utf-8")
        backend_config = (
            package_root / "anduinos-yubikey-manager/src/config.rs"
        ).read_text(encoding="utf-8")
        prerm = (
            package_root / "anduinos-yubikey-manager/scripts/prerm.sh"
        ).read_text(encoding="utf-8")

        policy = "etc/sudoers.d/90-anduinos-passwordless-admin"
        state = "var/lib/anduinos-passwordless-sudo/users"
        self.assertIn(f'Path("{policy}")', installer)
        self.assertIn(f'Path("{state}")', installer)
        self.assertIn(f'SUDOERS_DIR + "/{Path(policy).name}"', helper)
        self.assertIn(f'SUDO_STATE = "/{state}"', helper)
        self.assertIn(f'= "/{state}";', backend_config)
        self.assertNotIn(f'os.unlink("/{policy}")', prerm)

    def test_secure_shell_is_a_live_composition_not_an_upgrade_dependency(self):
        package_root = ROOT.parent
        desktop = ET.parse(
            package_root
            / "anduinos-desktop-core"
            / "anduinos-desktop-core.aosproj"
        ).getroot()
        core = ET.parse(
            package_root
            / "anduinos-core-system"
            / "anduinos-core-system.aosproj"
        ).getroot()
        live = ET.parse(
            package_root
            / "anduinos-live-settings"
            / "anduinos-live-settings.aosproj"
        ).getroot()

        dependencies = {
            item.get("Include") for item in desktop.iter("Dependency")
        }
        recommendations = {
            item.get("Include") for item in desktop.iter("Recommend")
        }
        suggestions = {
            item.get("Include") for item in desktop.iter("Suggest")
        }
        live_dependencies = {
            item.get("Include") for item in live.iter("Dependency")
        }
        self.assertNotIn("openssh-server", dependencies)
        self.assertNotIn("openssh-server", recommendations)
        self.assertIn("openssh-server", suggestions)
        self.assertIn("openssh-server", live_dependencies)

        preset = (
            package_root
            / "anduinos-core-system/assets/20-anduinos-security.preset"
        )
        self.assertFalse(preset.exists())
        files = {
            (item.get("Include"), item.get("Target"))
            for item in core.iter("IncludeFile")
        }
        self.assertNotIn(
            (
                "assets/20-anduinos-security.preset",
                "/usr/lib/systemd/system-preset/20-anduinos-security.preset",
            ),
            files,
        )

    def test_extended_codecs_are_optional_and_owned_by_one_metapackage(self):
        package_root = ROOT.parent
        desktop = ET.parse(
            package_root
            / "anduinos-desktop-core"
            / "anduinos-desktop-core.aosproj"
        ).getroot()
        multimedia = ET.parse(
            package_root
            / "anduinos-multimedia-codecs"
            / "anduinos-multimedia-codecs.aosproj"
        ).getroot()
        optional_packages = {
            "gstreamer1.0-plugins-bad",
            "gstreamer1.0-plugins-ugly",
            "gstreamer1.0-libav",
            "libavcodec-extra",
        }
        desktop_dependencies = {
            item.get("Include")
            for tag in ("Dependency", "Recommend")
            for item in desktop.iter(tag)
        }
        multimedia_dependencies = {
            item.get("Include")
            for item in multimedia.iter("Dependency")
        }
        self.assertTrue(optional_packages.isdisjoint(desktop_dependencies))
        self.assertEqual(multimedia_dependencies, optional_packages)

        apps = ET.parse(
            package_root
            / "anduinos-desktop-apps"
            / "anduinos-desktop-apps.aosproj"
        ).getroot()
        app_recommendations = {
            item.get("Include") for item in apps.iter("Recommend")
        }
        self.assertIn("celluloid", app_recommendations)
        self.assertIn("ffmpegthumbnailer", app_recommendations)

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
        self.assertEqual(
            root.findtext(".//SuiteShortNameMap"),
            "resolute-addon=resolute",
        )
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
                "anduinos-live-settings",
                "libxkbcommon0",
                "parted",
                "dosfstools",
                "efibootmgr",
                "gir1.2-nm-1.0",
                "network-manager",
                "netplan.io",
                "util-linux",
                "polkitd",
            }
            <= dependencies
        )
        self.assertNotIn("gnome-control-center", dependencies)
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
                elif relative == Path(
                    "usr/lib/anduinos-installer-beta/executor_cli.py"
                ):
                    path.write_text(
                        "isolate_mount_namespace()\n"
                        "sys.stdin.readline()\n"
                    )
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
