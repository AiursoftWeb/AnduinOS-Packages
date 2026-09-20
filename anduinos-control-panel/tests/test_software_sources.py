import importlib.machinery
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

from anduinos_control_panel.software_sources import (  # noqa: E402
    MIRRORS,
    current_mirror,
    replace_ubuntu_uris,
    select_fastest_mirror,
    simulated_upgrade_count,
    system_architecture,
    system_codename,
)

helper = importlib.machinery.SourceFileLoader(
    "software_source_helper",
    str(ROOT / "scripts/software-source-helper"),
).load_module()


class FakeResponse:
    status = 200

    def __init__(self, content=b"x" * 131072):
        self.content = content
        self.offset = 0

    def read(self, size):
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        return None


class AdvancingClock:
    def __init__(self):
        self.value = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.value += 0.01
            return self.value


class SoftwareSourceTests(unittest.TestCase):
    def test_system_metadata_is_strict_and_supports_both_release_architectures(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "os-release"
            release.write_text("NAME=AnduinOS\nVERSION_CODENAME=resolute\n")
            self.assertEqual(system_codename(release), "resolute")
        self.assertEqual(system_architecture("x86_64"), "amd64")
        self.assertEqual(system_architecture("aarch64"), "arm64")
        with self.assertRaises(RuntimeError):
            system_architecture("riscv64")

    def test_current_and_replacement_only_touch_ubuntu_deb822_uris(self):
        original = (
            "Types: deb\n"
            "URIs: https://archive.ubuntu.com/ubuntu/\n"
            "Suites: resolute resolute-updates\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ubuntu.sources"
            source.write_text(original)
            self.assertEqual(
                current_mirror(source), "https://archive.ubuntu.com/ubuntu/"
            )
        updated = replace_ubuntu_uris(original, MIRRORS[1])
        self.assertIn(f"URIs: {MIRRORS[1]}", updated)
        self.assertIn("Suites: resolute resolute-updates", updated)
        self.assertIn("Signed-By:", updated)
        with self.assertRaises(ValueError):
            replace_ubuntu_uris(original, "https://attacker.example/ubuntu/")

    def test_probe_uses_architecture_index_and_bandwidth_result(self):
        requested = []
        lock = threading.Lock()

        def opener(request, timeout):
            with lock:
                requested.append(request.full_url)
            return FakeResponse()

        result = select_fastest_mirror(
            "resolute",
            "arm64",
            candidates=(MIRRORS[0], MIRRORS[1]),
            opener=opener,
            clock=AdvancingClock(),
        )
        self.assertIn(result.uri, {MIRRORS[0], MIRRORS[1]})
        self.assertTrue(any("binary-arm64/Packages.gz" in url for url in requested))

    def test_simulated_update_summary_must_be_parseable(self):
        self.assertEqual(
            simulated_upgrade_count("3 upgraded, 0 newly installed, 0 to remove\n"),
            3,
        )
        with self.assertRaises(ValueError):
            simulated_upgrade_count("localized or malformed output")

    def test_privileged_switch_is_atomic_and_rolls_back_failed_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ubuntu.sources"
            backup = Path(directory) / "ubuntu.sources.bak"
            original = f"Types: deb\nURIs: {MIRRORS[0]}\nSuites: resolute\n"
            source.write_text(original)
            source.chmod(0o640)
            with (
                patch.object(helper, "SOURCE_PATH", source),
                patch.object(helper, "BACKUP_PATH", backup),
                patch.object(helper, "_apt") as apt,
            ):
                helper.switch_mirror(MIRRORS[1])
            self.assertIn(MIRRORS[1], source.read_text())
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(source.stat().st_mode & 0o777, 0o640)
            apt.assert_called_once_with("update")

            source.write_text(original)
            with (
                patch.object(helper, "SOURCE_PATH", source),
                patch.object(helper, "BACKUP_PATH", backup),
                patch.object(
                    helper,
                    "_apt",
                    side_effect=(RuntimeError("bad mirror"), None),
                ) as apt,
            ):
                with self.assertRaisesRegex(RuntimeError, "original source was restored"):
                    helper.switch_mirror(MIRRORS[1])
            self.assertEqual(source.read_text(), original)
            self.assertEqual(apt.call_count, 2)

    def test_privileged_entry_point_requires_root_and_fixed_actions(self):
        with patch.object(helper.os, "geteuid", return_value=1000):
            self.assertEqual(helper.main(["refresh"]), 77)
        with (
            patch.object(helper.os, "geteuid", return_value=0),
            patch.object(helper, "_apt") as apt,
        ):
            self.assertEqual(helper.main(["refresh"]), 0)
            apt.assert_called_once_with("update")
            self.assertEqual(helper.main(["shell", "anything"]), 64)


if __name__ == "__main__":
    unittest.main()
