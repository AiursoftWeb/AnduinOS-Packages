import importlib.machinery
import pathlib
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).parents[1] / "assets" / "anduinos-oobe"
oobe = importlib.machinery.SourceFileLoader(
    "anduinos_oobe_startup", str(SCRIPT)
).load_module()


class StartupTests(unittest.TestCase):
    def test_live_detection_requires_the_dracut_runtime_contract(self):
        with (
            mock.patch.object(
                oobe.os.path,
                "isfile",
                side_effect=lambda path: path in {
                    "/run/anduinos-live/environment",
                    "/run/anduinos-live/rootfs.squashfs",
                },
            ),
            mock.patch.object(oobe.os.path, "isdir", return_value=True),
        ):
            self.assertTrue(oobe.is_live_environment())

        with mock.patch.object(oobe.os.path, "isfile", return_value=False):
            self.assertFalse(oobe.is_live_environment())


if __name__ == "__main__":
    unittest.main()
