import tempfile
import threading
import unittest
from unittest.mock import Mock
from pathlib import Path

from helpers import valid_plan
from installer_core.network import (
    DetectNetworkConnectivityStep,
    RecheckNetworkConnectivityStep,
    probe_ubuntu_archive,
)
from installer_core.steps import InstallContext, StepSkipped


class FakeResponse:
    status = 206

    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self, size: int) -> bytes:
        return self.payload[:size]

    def close(self) -> None:
        return None


class NetworkDetectionTests(unittest.TestCase):
    def test_every_endpoint_is_logged_on_the_calling_thread(self):
        logs = []
        caller = threading.get_ident()

        def opener(request, timeout):
            if "failed.example" in request.full_url:
                raise TimeoutError("unreachable")
            return FakeResponse(b"Codename: resolute\n")

        endpoint = probe_ubuntu_archive(
            "resolute",
            candidates=("http://good.example/", "http://failed.example/"),
            opener=opener,
            log=lambda message: logs.append((threading.get_ident(), message)),
        )
        self.assertEqual(endpoint, "http://good.example/")
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(thread == caller for thread, _ in logs))
        self.assertTrue(any("TimeoutError" in message for _, message in logs))
        self.assertTrue(any("HTTP 206; verified" in message for _, message in logs))

    def test_successful_initial_probe_does_not_retry(self):
        detector = Mock(side_effect=AssertionError("unexpected retry"))
        context = InstallContext(
            valid_plan(), lambda _message: None, {"network_online": True},
        )
        with self.assertRaises(StepSkipped):
            RecheckNetworkConnectivityStep(detector=detector).execute(context)
        detector.assert_not_called()

    def test_probe_logs_failure_kind_status_content_and_elapsed_time(self):
        for response, expected in (
            (TimeoutError("timed out"), "TimeoutError: timed out"),
            (FakeResponse(b"<html>Login</html>"), "codename mismatch"),
            (Mock(status=503, read=Mock(return_value=b"")), "HTTP 503"),
        ):
            with self.subTest(expected=expected):
                logs = []
                opener = (
                    Mock(side_effect=response)
                    if isinstance(response, Exception)
                    else Mock(return_value=response)
                )
                self.assertIsNone(probe_ubuntu_archive(
                    "resolute", candidates=("http://archive.example/ubuntu/",),
                    opener=opener, log=logs.append,
                ))
                self.assertEqual(len(logs), 1)
                self.assertIn(expected, logs[0])
                self.assertIn("elapsed=", logs[0])
                self.assertIn("http://archive.example/ubuntu/dists/resolute/Release", logs[0])

    def test_probe_closes_response_when_read_fails(self):
        response = Mock(status=200, read=Mock(side_effect=TimeoutError("read")))
        logs = []
        self.assertIsNone(probe_ubuntu_archive(
            "resolute", candidates=("http://archive.example/ubuntu/",),
            opener=Mock(return_value=response), log=logs.append,
        ))
        response.close.assert_called_once()
        self.assertIn("TimeoutError", logs[0])

    def test_download_boundary_rechecks_only_failed_initial_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "os-release"
            release.write_text("VERSION_CODENAME=resolute\n")
            detector = Mock(side_effect=[None, "http://archive.example/ubuntu/"])
            context = InstallContext(valid_plan(), lambda _message: None)
            initial = DetectNetworkConnectivityStep(os_release=release, detector=detector)
            retry = RecheckNetworkConnectivityStep(os_release=release, detector=detector)
            with self.assertRaises(RuntimeError):
                initial.execute(context)
            retry.execute(context)
            retry.verify(context)
            self.assertTrue(context.values["network_online"])
            self.assertEqual(detector.call_count, 2)
            with self.assertRaises(StepSkipped):
                retry.execute(context)
            self.assertEqual(detector.call_count, 2)

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
