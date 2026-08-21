import ast
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_readme_defines_scope_before_directory_layout(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(readme.index("## Scope contract"), readme.index("## Directory structure"))
        for forbidden_scope in (
            "detect NVIDIA hardware",
            "install NVIDIA, Xbox, audio, printing",
            "accept arbitrary commands",
            "own OOBE navigation",
        ):
            self.assertIn(forbidden_scope, readme)

    def test_package_owns_the_secure_boot_runtime_dependencies(self):
        project = ET.parse(ROOT / "anduinos-secureboot-toolkit.aosproj").getroot()
        dependencies = {item.get("Include") for item in project.iter("Dependency")}
        suggestions = {item.get("Include") for item in project.iter("Suggest")}
        self.assertTrue(
            {"mokutil", "openssl", "shim-signed", "kmod", "pkexec"}
            <= dependencies
        )
        self.assertNotIn("dkms", dependencies)
        self.assertIn("dkms", suggestions)

    def test_package_version_carries_the_optional_dkms_transition(self):
        project = ET.parse(ROOT / "anduinos-secureboot-toolkit.aosproj").getroot()
        self.assertEqual(
            "2.0.2-2+$(SuiteShortName)",
            project.findtext(".//PackageVersion"),
        )

    def test_helper_is_fixed_and_does_not_evaluate_shell(self):
        helper = (ROOT / "scripts/anduinos-secureboot-helper").read_text()
        operations = (ROOT / "src/anduinos_secureboot/operations.py").read_text()
        self.assertNotIn("shell=True", helper + operations)
        self.assertNotIn("bash -c", helper + operations)
        self.assertNotIn("apt-get", helper + operations)
        self.assertNotIn("ubuntu-drivers", helper + operations)

    def test_polkit_only_authorizes_the_fixed_helper(self):
        policy = ET.parse(ROOT / "data/com.anduinos.SecureBootToolkit.policy")
        annotations = {
            item.attrib.get("key"): (item.text or "").strip()
            for item in policy.findall(".//annotate")
        }
        self.assertEqual(
            annotations["org.freedesktop.policykit.exec.path"],
            "/usr/libexec/anduinos-secureboot-helper",
        )

    def test_python_sources_compile(self):
        for source in [*ROOT.glob("scripts/*"), *ROOT.glob("src/**/*.py")]:
            if not source.is_file():
                continue
            compile(source.read_text(encoding="utf-8"), str(source), "exec")

    def test_status_cli_uses_state_aware_schema(self):
        cli = (ROOT / "scripts/anduinos-securebootctl").read_text()
        self.assertIn('{"schema": 2, "secure_boot":', cli)

    def test_ui_translation_function_is_not_shadowed(self):
        source = (ROOT / "src/anduinos_secureboot/ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "create_secure_boot_page"
        )
        stores = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Name)
            and node.id == "_"
            and isinstance(node.ctx, ast.Store)
        ]
        self.assertEqual(len(stores), 1)

    def test_ui_never_offers_module_repair_without_dkms(self):
        source = (ROOT / "src/anduinos_secureboot/ui.py").read_text(encoding="utf-8")
        self.assertIn("secure_boot.dkms_available and not dkms.ready", source)


if __name__ == "__main__":
    unittest.main()
