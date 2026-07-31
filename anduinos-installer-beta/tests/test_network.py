import tempfile
import unittest
from pathlib import Path

from helpers import valid_plan
from installer_core.network import (
    DetectNetworkConnectivityStep,
    probe_ubuntu_archive,
)
from installer_core.steps import InstallContext


class FakeResponse:
    status = 206

    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self, size: int) -> bytes:
        return self.payload[:size]

    def close(self) -> None:
        return None


class NetworkDetectionTests(unittest.TestCase):
    def test_probe_requires_a_real_release_file_for_the_codename(self):
        requested = []

        def opener(request, timeout):
            requested.append((request.full_url, timeout))
            return FakeResponse(
                b"Origin: Ubuntu\nSuite: resolute\nCodename: resolute\n"
            )

        endpoint = probe_ubuntu_archive(
            "resolute",
            candidates=("http://archive.example/ubuntu/",),
            opener=opener,
        )
        self.assertEqual(endpoint, "http://archive.example/ubuntu/")
        self.assertEqual(requested[0][1], 4)
        self.assertTrue(requested[0][0].endswith("/dists/resolute/Release"))

    def test_captive_portal_response_is_not_treated_as_online(self):
        endpoint = probe_ubuntu_archive(
            "resolute",
            candidates=("http://portal.example/ubuntu/",),
            opener=lambda _request, timeout: FakeResponse(
                b"<html>Please sign in</html>"
            ),
        )
        self.assertIsNone(endpoint)

    def test_step_persists_online_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            os_release = Path(directory) / "os-release"
            os_release.write_text(
                "NAME=AnduinOS\nVERSION_CODENAME=resolute\n",
                encoding="utf-8",
            )
            logs = []
            context = InstallContext(valid_plan(), logs.append)
            step = DetectNetworkConnectivityStep(
                os_release=os_release,
                detector=lambda codename: (
                    "http://archive.example/ubuntu/"
                    if codename == "resolute"
                    else None
                ),
            )
            step.execute(context)
            step.verify(context)

        self.assertTrue(context.values["network_online"])
        self.assertEqual(
            context.values["network_endpoint"],
            "http://archive.example/ubuntu/",
        )
        self.assertTrue(any("online via" in message for message in logs))

    def test_step_marks_offline_before_emitting_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            os_release = Path(directory) / "os-release"
            os_release.write_text(
                "VERSION_CODENAME=resolute\n", encoding="utf-8"
            )
            context = InstallContext(valid_plan(), lambda _message: None)
            step = DetectNetworkConnectivityStep(
                os_release=os_release,
                detector=lambda _codename: None,
            )
            with self.assertRaisesRegex(RuntimeError, "Offline mode"):
                step.execute(context)

        self.assertIs(context.values["network_online"], False)
        self.assertIsNone(context.values["network_endpoint"])


if __name__ == "__main__":
    unittest.main()
