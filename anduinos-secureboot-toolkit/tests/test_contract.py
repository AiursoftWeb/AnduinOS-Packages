import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anduinos_secureboot.model import DkmsState, SecureBootState, SecureBootStatus


class ContractTests(unittest.TestCase):
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

    def test_status_cli_distinguishes_unknown_disabled_and_enabled(self):
        # The CLI is executed; only the hardware inspection boundary is replaced.
        for status in SecureBootStatus:
            state = SecureBootState(
                status is SecureBootStatus.ENABLED, True, True, False,
                "certificate-serial", status=status,
            )
            for arguments in ([], ["status"], ["status", "--json"]):
                with self.subTest(status=status, arguments=arguments):
                    output = io.StringIO()
                    with (
                        patch("sys.argv", ["anduinos-securebootctl", *arguments]),
                        patch("anduinos_secureboot.inspect_secure_boot", return_value=state),
                        patch("anduinos_secureboot.inspect_dkms", return_value=DkmsState(
                            modules=("driver",), untrusted_modules=("driver",),
                        )) as inspect_dkms,
                        contextlib.redirect_stdout(output),
                    ):
                        runpy.run_path(str(ROOT / "scripts/anduinos-securebootctl"), run_name="__main__")
                    payload = json.loads(output.getvalue())
                    self.assertEqual(payload["schema"], 2)  # Public protocol, not package revision.
                    self.assertEqual(payload["secure_boot"]["status"], status.value)
                    self.assertEqual(payload["secure_boot"]["enabled"], state.enabled)
                    self.assertEqual(payload["dkms"]["untrusted_modules"], ["driver"])
                    inspect_dkms.assert_called_once_with(state)

    def test_status_cli_rejects_invalid_commands_without_probing_hardware(self):
        for arguments in (["repair"], ["status", "extra"], ["status; reboot"]):
            with (
                self.subTest(arguments=arguments),
                patch("sys.argv", ["anduinos-securebootctl", *arguments]),
                patch("anduinos_secureboot.inspect_secure_boot") as inspect,
                patch("anduinos_secureboot.inspect_dkms") as inspect_dkms,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(ROOT / "scripts/anduinos-securebootctl"), run_name="__main__")
                self.assertNotEqual(result.exception.code, 0)
                inspect.assert_not_called()
                inspect_dkms.assert_not_called()


if __name__ == "__main__":
    unittest.main()
