"""Preserve signed payloads while excluding Ubuntu's signing/deployment hooks."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "prepare.sh"


class PrepareTests(unittest.TestCase):
    def prepare(self, stage):
        return subprocess.run(["bash", str(SCRIPT)],
                              env={**os.environ, "APKG_STAGE_DIR": str(stage)},
                              capture_output=True, text=True)

    def test_both_architectures_preserve_only_the_owned_payload(self):
        for suite in ("resolute-addon", "stonking-addon"):
            for arch, suffix in (("amd64", "x64"), ("arm64", "aa64")):
                with self.subTest(suite=suite, arch=arch), tempfile.TemporaryDirectory() as tmp:
                    stage = Path(tmp) / f"{suite}_{arch}"
                    files = {
                        f"usr/lib/shim/shim{suffix}.efi.signed.latest": b"signed shim\x00\xff",
                        f"usr/lib/shim/mm{suffix}.efi": b"signed MokManager\x00\xff",
                        "usr/lib/shim/is-not-revoked": b"#!/bin/sh\nexit 0\n",
                        "usr/share/doc/shim-signed/copyright": b"upstream copyright",
                        "usr/sbin/update-secureboot-policy": b"must not run",
                        "etc/kernel/postinst.d/zz-shim": b"must not run",
                        "usr/lib/shim/mok/openssl.cnf": b"must not ship",
                        f"usr/lib/shim/fb{suffix}.efi": b"must not ship",
                        "DEBIAN/control": b"Apkg owns package metadata",
                    }
                    for name, data in files.items():
                        path = stage / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                    (stage / "usr/lib/shim/is-not-revoked").chmod(0o755)
                    result = self.prepare(stage)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected = {name: data for name, data in files.items()
                                if name.startswith("DEBIAN/") or name in (
                                    f"usr/lib/shim/shim{suffix}.efi.signed.latest",
                                    f"usr/lib/shim/mm{suffix}.efi", "usr/lib/shim/is-not-revoked")}
                    expected["usr/share/doc/anduinos-shim-signed/upstream/copyright"] = b"upstream copyright"
                    actual = {str(path.relative_to(stage)): path.read_bytes()
                              for path in stage.rglob("*") if path.is_file()}
                    self.assertEqual(actual, expected)
                    self.assertTrue(os.access(stage / "usr/lib/shim/is-not-revoked", os.X_OK))

    def test_missing_payload_fails_before_replacing_upstream_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stonking-addon_arm64"
            policy = stage / "usr/sbin/update-secureboot-policy"
            policy.parent.mkdir(parents=True)
            policy.write_text("original")
            self.assertNotEqual(self.prepare(stage).returncode, 0)
            self.assertEqual(policy.read_text(), "original")
