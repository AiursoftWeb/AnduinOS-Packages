import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from anduinos_rescue_center.privileged import main


class PrivilegedDispatchTests(unittest.TestCase):
    def test_boot_repair_reaches_only_the_fixed_disk_identity_action(self):
        output = StringIO()
        with (patch("anduinos_rescue_center.privileged.os.geteuid", return_value=0),
              patch("anduinos_rescue_center.privileged.repair_boot",
                    return_value={"schema": 1, "issues": []}) as repair,
              redirect_stdout(output)):
            self.assertEqual(main(["repair-boot", "/dev/sda2", "a" * 64, "b" * 64]), 0)
        repair.assert_called_once_with("/dev/sda2", "a" * 64, "b" * 64)
        self.assertEqual(json.loads(output.getvalue())["schema"], 1)

    def test_emergency_shell_keeps_terminal_stream_instead_of_json_protocol(self):
        output = StringIO()
        with (patch("anduinos_rescue_center.privileged.os.geteuid", return_value=0),
              patch("anduinos_rescue_center.privileged.emergency_shell", return_value=0) as shell,
              redirect_stdout(output)):
            self.assertEqual(main(["shell", "/dev/sda2", "a" * 64]), 0)
        shell.assert_called_once_with("/dev/sda2", "a" * 64)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
