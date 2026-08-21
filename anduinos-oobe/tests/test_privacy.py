import pathlib
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import importlib.machinery


SCRIPT = pathlib.Path(__file__).parents[1] / "assets" / "anduinos-oobe"
oobe = importlib.machinery.SourceFileLoader(
    "anduinos_oobe_privacy", str(SCRIPT)
).load_module()
HELPER_SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "network-service-helper"
helper = importlib.machinery.SourceFileLoader(
    "anduinos_oobe_network_helper", str(HELPER_SCRIPT)
).load_module()
POLICY = pathlib.Path(__file__).parents[1] / "data" / "com.anduinos.oobe.policy"


class PrivacyTests(unittest.TestCase):
    def completed(self, stdout="", returncode=0):
        return subprocess.CompletedProcess([], returncode, stdout, "")

    def test_masked_avahi_is_available_but_disabled(self):
        with (
            mock.patch.object(oobe.os.path, "isfile", return_value=True),
            mock.patch.object(
                oobe.subprocess,
                "run",
                side_effect=[
                    self.completed("masked\n"),
                    self.completed("masked\n", 1),
                    self.completed("masked\n", 1),
                ],
            ),
        ):
            self.assertEqual(oobe.get_mdns_control_state(), (True, False))

    def test_missing_avahi_is_unavailable(self):
        with (
            mock.patch.object(oobe.os.path, "isfile", return_value=True),
            mock.patch.object(
                oobe.subprocess, "run",
                return_value=self.completed("not-found\n"),
            ),
        ):
            self.assertEqual(oobe.get_mdns_control_state(), (False, False))

    def test_toggle_uses_only_the_fixed_privileged_helper(self):
        with mock.patch.object(
            oobe.subprocess, "run", return_value=self.completed()
        ) as run:
            oobe.set_mdns_enabled(False)

        run.assert_called_once_with(
            [
                "pkexec",
                "/usr/libexec/anduinos-oobe/network-service-helper",
                "set-mdns-enabled",
                "false",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_helper_controls_only_avahi_units(self):
        self.assertEqual(
            helper.MDNS_UNITS,
            ("avahi-daemon.service", "avahi-daemon.socket"),
        )
        with mock.patch.object(helper, "run") as run:
            helper.set_mdns_enabled(False)
        run.assert_called_once_with(
            ["systemctl", "mask", "--now", *helper.MDNS_UNITS]
        )

    def test_policy_authorizes_only_oobe_fixed_helper(self):
        root = ET.parse(POLICY).getroot()
        action = root.find(
            "./action[@id='com.anduinos.oobe.manage-network-discovery']"
        )
        self.assertIsNotNone(action)
        annotations = {
            node.attrib["key"]: node.text for node in action.findall("annotate")
        }
        self.assertEqual(
            annotations.get("org.freedesktop.policykit.exec.path"),
            "/usr/libexec/anduinos-oobe/network-service-helper",
        )
        self.assertNotIn("org.freedesktop.policykit.exec.allow_gui", annotations)

    def test_bash_predictions_are_enabled_without_managed_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            bashrc = pathlib.Path(root) / ".bashrc"
            self.assertTrue(oobe.bash_command_predictions_enabled(bashrc))
            self.assertFalse(
                oobe.set_bash_command_predictions_enabled(True, bashrc)
            )
            self.assertFalse(bashrc.exists())

    def test_disabling_bash_predictions_appends_one_managed_block(self):
        with tempfile.TemporaryDirectory() as root:
            bashrc = pathlib.Path(root) / ".bashrc"
            bashrc.write_text("export PATH=\"$HOME/bin:$PATH\"\n")

            self.assertTrue(
                oobe.set_bash_command_predictions_enabled(False, bashrc)
            )
            self.assertFalse(oobe.bash_command_predictions_enabled(bashrc))
            first = bashrc.read_text()
            self.assertIn("export PATH=", first)
            self.assertEqual(first.count(oobe.BASH_GUESS_BEGIN), 1)
            self.assertIn("export ANDUINOS_GUESS_COMMAND=0", first)
            self.assertFalse(
                oobe.set_bash_command_predictions_enabled(False, bashrc)
            )
            self.assertEqual(bashrc.read_text(), first)

    def test_enabling_removes_only_the_oobe_managed_block(self):
        with tempfile.TemporaryDirectory() as root:
            bashrc = pathlib.Path(root) / ".bashrc"
            bashrc.write_text("alias keep-me='printf safe'\n")
            bashrc.chmod(0o640)
            oobe.set_bash_command_predictions_enabled(False, bashrc)

            self.assertTrue(
                oobe.set_bash_command_predictions_enabled(True, bashrc)
            )
            self.assertTrue(oobe.bash_command_predictions_enabled(bashrc))
            self.assertEqual(bashrc.read_text(), "alias keep-me='printf safe'\n\n")
            self.assertEqual(bashrc.stat().st_mode & 0o777, 0o640)

    def test_manual_disable_is_respected_and_can_be_explicitly_overridden(self):
        with tempfile.TemporaryDirectory() as root:
            bashrc = pathlib.Path(root) / ".bashrc"
            bashrc.write_text("export ANDUINOS_GUESS_COMMAND='0'\n")
            self.assertFalse(oobe.bash_command_predictions_enabled(bashrc))

            oobe.set_bash_command_predictions_enabled(True, bashrc)
            self.assertTrue(oobe.bash_command_predictions_enabled(bashrc))
            contents = bashrc.read_text()
            self.assertIn("export ANDUINOS_GUESS_COMMAND='0'", contents)
            self.assertIn("export ANDUINOS_GUESS_COMMAND=1", contents)

    def test_bashrc_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            target = pathlib.Path(root) / "shared-bashrc"
            target.write_text("# shared\n")
            bashrc = pathlib.Path(root) / ".bashrc"
            bashrc.symlink_to(target)

            oobe.set_bash_command_predictions_enabled(False, bashrc)
            self.assertTrue(bashrc.is_symlink())
            self.assertIn("ANDUINOS_GUESS_COMMAND=0", target.read_text())

    def test_failed_atomic_replace_preserves_the_original_bashrc(self):
        with tempfile.TemporaryDirectory() as root:
            bashrc = pathlib.Path(root) / ".bashrc"
            bashrc.write_text("# original\n")
            with mock.patch.object(
                oobe.os, "replace", side_effect=OSError("simulated failure")
            ):
                with self.assertRaises(OSError):
                    oobe.set_bash_command_predictions_enabled(False, bashrc)
            self.assertEqual(bashrc.read_text(), "# original\n")
            self.assertEqual(
                list(pathlib.Path(root).glob(".bashrc.anduinos-*")), []
            )


if __name__ == "__main__":
    unittest.main()
