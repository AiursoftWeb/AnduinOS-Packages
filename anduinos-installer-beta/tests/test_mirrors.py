import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import valid_plan
from installer_core.mirrors import (
    MirrorMeasurement,
    SelectFastestAptMirrorStep,
    select_fastest_mirror,
)
from installer_core.model import Architecture
from installer_core.steps import InstallContext, StepWarning


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


class MirrorSelectionTests(unittest.TestCase):
    def test_offline_step_does_not_probe_or_modify_sources(self):
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            {"network_online": False},
        )
        with (
            patch("installer_core.mirrors.select_fastest_mirror") as select,
            self.assertRaisesRegex(StepWarning, "offline"),
        ):
            SelectFastestAptMirrorStep().execute(context)
        select.assert_not_called()

    def test_arm64_bandwidth_probe_uses_arm64_then_amd64_fallback(self):
        requested = []
        lock = threading.Lock()

        def opener(request, timeout):
            url = request.full_url
            with lock:
                requested.append(url)
            if "binary-arm64" in url:
                raise OSError("architecture index unavailable")
            return FakeResponse()

        result = select_fastest_mirror(
            "resolute",
            Architecture.ARM64,
            candidates=(
                "http://one.example/ubuntu/",
                "https://two.example/ubuntu/",
            ),
            opener=opener,
            clock=AdvancingClock(),
        )
        self.assertIn(result.uri, {
            "http://one.example/ubuntu/",
            "https://two.example/ubuntu/",
        })
        self.assertTrue(any("binary-arm64/Packages.gz" in url for url in requested))
        self.assertTrue(any("Contents-amd64.gz" in url for url in requested))

    def test_step_only_replaces_ubuntu_deb822_uris_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            (target / "etc/os-release").write_text(
                "NAME=AnduinOS\nVERSION_CODENAME=resolute\n"
            )
            source = target / "etc/apt/sources.list.d/ubuntu.sources"
            source.parent.mkdir(parents=True)
            source.write_text(
                "Types: deb\n"
                "URIs: http://archive.ubuntu.com/ubuntu/\n"
                "Suites: resolute resolute-updates\n"
                "Components: main restricted universe multiverse\n"
                "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
            )
            source.chmod(0o640)
            anduinos = source.parent / "anduinos.sources"
            anduinos.write_text(
                "URIs: https://packages.anduinos.com/artifacts/anduinos/\n"
            )
            context = InstallContext(
                valid_plan(), lambda _message: None, {"target": target}
            )
            measurement = MirrorMeasurement(
                "http://fast.example/ubuntu/", 12.0, 250.0
            )
            with patch(
                "installer_core.mirrors.select_fastest_mirror",
                return_value=measurement,
            ):
                step = SelectFastestAptMirrorStep()
                step.execute(context)
                step.verify(context)

            content = source.read_text()
            self.assertIn("URIs: http://fast.example/ubuntu/", content)
            self.assertIn("Suites: resolute resolute-updates", content)
            self.assertIn("Signed-By:", content)
            self.assertEqual(source.stat().st_mode & 0o777, 0o640)
            self.assertIn("packages.anduinos.com", anduinos.read_text())
            self.assertIn("archive.ubuntu.com", context.values["apt_mirror_original"].decode())
