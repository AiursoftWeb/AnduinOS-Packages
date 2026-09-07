from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

class PackageContractTests(unittest.TestCase):
    def test_xpadneo_package_scripts_propagate_real_dkms_failures(self):
        package = ROOT
        postinst = (package / "scripts/postinst.sh").read_text()
        prerm = (package / "scripts/prerm.sh").read_text()
        self.assertNotIn("|| true", postinst)
        self.assertNotIn("|| true", prerm)
        self.assertIn('dkms build -m "$PKG_NAME"', postinst)
        self.assertIn('dkms install -m "$PKG_NAME"', postinst)
        self.assertIn('dkms status -m "$PKG_NAME" -v "$VERSION"', postinst)
        self.assertIn('-k "$KERNEL_RELEASE"', postinst)
        self.assertIn('*": installed"*)', postinst)
        self.assertIn('dkms status -m "$PKG_NAME" -v "$VERSION"', prerm)
        self.assertNotIn('/var/lib/dkms/', prerm)


if __name__ == "__main__":
    unittest.main()
