#!/usr/bin/env python3
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SETUP = PROJECT / "assets/anduinos-live-session-setup"

class LiveSettingsPackageContractTests(unittest.TestCase):

    def test_valid_and_hostile_regional_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cmdline = root / "cmdline"
            (root / "run/anduinos-live").mkdir(parents=True)
            (root / "run/anduinos-live/environment").write_text(
                "ANDUINOS_LIVE=1\n", encoding="utf-8"
            )
            for timezone in ("Asia/Shanghai", "Etc/UTC"):
                zone = root / "usr/share/zoneinfo" / timezone
                zone.parent.mkdir(parents=True, exist_ok=True)
                zone.touch()
            symbols = root / "usr/share/X11/xkb/symbols"
            symbols.mkdir(parents=True)
            for layout in ("us", "fr"):
                (symbols / layout).touch()
            env = {
                **os.environ,
                "ANDUINOS_LIVE_ROOT": str(root),
                "ANDUINOS_LIVE_CMDLINE_FILE": str(cmdline),
                "ANDUINOS_LIVE_TEST_MODE": "1",
            }

            cmdline.write_text(
                "rd.anduinos.live=1 locale=zh_CN.UTF-8 "
                "timezone=Asia/Shanghai rd.anduinos.keyboard=fr "
                "hostname=anduinos quiet\n",
                encoding="utf-8",
            )
            subprocess.run(["/bin/sh", SETUP], env=env, check=True)
            self.assertEqual(
                (root / "etc/timezone").read_text(encoding="utf-8"),
                "Asia/Shanghai\n",
            )
            self.assertEqual(
                os.readlink(root / "etc/localtime"),
                "/usr/share/zoneinfo/Asia/Shanghai",
            )
            self.assertEqual(
                (root / "etc/default/locale").read_text(encoding="utf-8"),
                'LANG="zh_CN.UTF-8"\n',
            )
            self.assertEqual(
                (root / "etc/default/keyboard").read_text(encoding="utf-8"),
                'XKBMODEL="pc105"\n'
                'XKBLAYOUT="fr"\n'
                'XKBVARIANT=""\n'
                'XKBOPTIONS=""\n'
                'BACKSPACE="guess"\n',
            )
            self.assertIn(
                "ANDUINOS_LIVE_KEYBOARD=fr\n",
                (root / "run/anduinos-live/environment").read_text(
                    encoding="utf-8"
                ),
            )

            for value in ("../../etc/passwd", "/etc/passwd", "Asia/Bad;Name"):
                with self.subTest(value=value):
                    cmdline.write_text(
                        f"rd.anduinos.live=1 timezone={value}\n", encoding="utf-8"
                    )
                    subprocess.run(["/bin/sh", SETUP], env=env, check=True)
                    self.assertEqual(
                        (root / "etc/timezone").read_text(encoding="utf-8"),
                        "Etc/UTC\n",
                    )

            for value in ("missing", "../../fr", "fr;touch", "-option"):
                with self.subTest(keyboard=value):
                    cmdline.write_text(
                        f"rd.anduinos.live=1 rd.anduinos.keyboard={value}\n",
                        encoding="utf-8",
                    )
                    subprocess.run(["/bin/sh", SETUP], env=env, check=True)
                    self.assertIn(
                        'XKBLAYOUT="us"\n',
                        (root / "etc/default/keyboard").read_text(
                            encoding="utf-8"
                        ),
                    )

    def test_setup_is_valid_posix_shell(self):
        subprocess.run(["/bin/sh", "-n", SETUP], check=True)

if __name__ == "__main__":
    unittest.main()
