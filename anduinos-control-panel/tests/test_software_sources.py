import importlib.machinery
from pathlib import Path
import tempfile
import subprocess
import threading
import unittest
import urllib.error
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

from anduinos_control_panel.software_sources import (  # noqa: E402
    MIRRORS,
    current_mirror,
    measure_mirrors,
    probe_mirror,
    replace_ubuntu_uris,
    select_fastest_mirror,
    simulated_upgrade_count,
    system_architecture,
    system_codename,
    validate_mirror_uri,
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
        custom = "https://example.org/ubuntu/"
        self.assertIn(f"URIs: {custom}", replace_ubuntu_uris(original, custom))
        for invalid in (
            "file:///tmp/repository", "https://user:pass@example.org/ubuntu/",
            "https://example.org/ubuntu/?x=1", "https://example.org/../other/",
            "https://example.org/ubuntu/?", "https://example.org/ubuntu/#",
            "https://example.org/ubuntu/\nURIs: https://other.example/",
            "https://example.org:bad/ubuntu/", "https://[broken/ubuntu/",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_mirror_uri(invalid)

    def test_custom_mirror_requires_release_and_architecture_before_switch(self):
        requested = []

        def opener(request, timeout):
            requested.append(request.full_url)
            return FakeResponse(b"valid")

        probe_mirror("https://example.org/ubuntu", "resolute", "arm64", opener)
        self.assertEqual(
            requested,
            [
                "https://example.org/ubuntu/dists/resolute/Release",
                "https://example.org/ubuntu/dists/resolute/main/binary-arm64/Packages.gz",
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ubuntu.sources"
            original = f"Types: deb\nURIs: {MIRRORS[0]}\nSuites: resolute\n"
            source.write_text(original)
            with (
                patch.object(helper, "SOURCE_PATH", source),
                patch.object(helper, "probe_mirror", side_effect=RuntimeError("bad")),
                patch.object(helper, "_apt") as apt,
            ):
                with self.assertRaises(RuntimeError):
                    helper.switch_mirror("https://example.org/ubuntu/")
            self.assertEqual(source.read_text(), original)
            apt.assert_not_called()
            with (
                patch.object(helper, "SOURCE_PATH", source),
                patch.object(helper, "BACKUP_PATH", Path(directory) / "ubuntu.sources.bak"),
                patch.object(helper, "probe_mirror") as probe,
                patch.object(helper, "_apt") as apt,
            ):
                helper.switch_mirror("https://example.org/ubuntu")
            self.assertIn("URIs: https://example.org/ubuntu/", source.read_text())
            probe.assert_called_once()
            apt.assert_called_once_with("update")

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
        measurements = measure_mirrors(
            "resolute", "arm64", candidates=(MIRRORS[0], MIRRORS[1]),
            opener=opener, clock=AdvancingClock(),
        )
        self.assertEqual({item.uri for item in measurements}, {MIRRORS[0], MIRRORS[1]})

    def test_head_rejection_falls_back_to_a_small_get(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            if request.get_method() == "HEAD":
                raise urllib.error.HTTPError(request.full_url, 404, "", {}, None)
            return FakeResponse()

        result = select_fastest_mirror(
            "resolute", "amd64", candidates=("https://mirror.example/ubuntu/",),
            opener=opener, clock=AdvancingClock(),
        )
        self.assertEqual(result.uri, "https://mirror.example/ubuntu/")
        self.assertEqual(requests[0].get_method(), "HEAD")
        self.assertEqual(requests[1].get_method(), "GET")
        self.assertEqual(requests[1].get_header("Range"), "bytes=0-0")

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

    def test_partial_index_failure_triggers_source_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ubuntu.sources"
            original = f"URIs: {MIRRORS[0]}\nSuites: stonking\n"
            source.write_text(original)
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                # APT only returns nonzero for a partial acquisition failure
                # when strict index handling is requested.
                strict = "APT::Update::Error-Mode=any" in command
                code = 100 if len(calls) == 1 and strict else 0
                return subprocess.CompletedProcess(command, code)

            with (
                patch.object(helper, "SOURCE_PATH", source),
                patch.object(helper, "BACKUP_PATH", source.with_suffix(".bak")),
                patch.object(helper.subprocess, "run", side_effect=run),
            ):
                with self.assertRaisesRegex(RuntimeError, "original source was restored"):
                    helper.switch_mirror(MIRRORS[1])
            self.assertEqual(source.read_text(), original)
            self.assertEqual(len(calls), 2)

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


class FlathubSourceTests(unittest.TestCase):
    def test_builtins_are_official_sjtu_ustc_and_custom_addresses_are_validated(self):
        from anduinos_control_panel.software_sources import FLATHUB_SOURCES, validate_flathub_uri
        self.assertEqual(tuple(FLATHUB_SOURCES), ("official", "sjtu", "ustc"))
        self.assertEqual(validate_flathub_uri("https://custom.example/repo/"), "https://custom.example/repo")
        for uri in ["file:///tmp/repo", "https://user:pass@example/repo", "https://example/repo?key=x", "--no-gpg-verify"]:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                validate_flathub_uri(uri)

    def test_custom_switch_retains_signatures_and_only_changes_the_existing_remote(self):
        uri = "https://custom.example/repo"
        with patch.object(helper, "probe_flathub") as probe, patch.object(helper, "current_flathub", side_effect=["https://dl.flathub.org/repo", uri]), patch.object(helper, "_flatpak") as run:
            helper.switch_flathub(uri + "/")
        probe.assert_called_once_with(uri)
        self.assertEqual(run.call_args_list[0].args, ("remote-modify", "flathub", "--enable", "--gpg-verify", "--url=" + uri))
        self.assertNotIn("remote-add", str(run.call_args_list))
        self.assertNotIn("gpg-import", str(run.call_args_list))

    def test_failed_switch_readback_restores_original_custom_url(self):
        original = "https://old.example/repo"
        with patch.object(helper, "probe_flathub"), patch.object(helper, "current_flathub", side_effect=[original, "https://wrong.example/repo", original]), patch.object(helper, "_flatpak") as run:
            with self.assertRaisesRegex(RuntimeError, "original source was restored"):
                helper.switch_flathub("sjtu")
        self.assertEqual(run.call_args_list[-1].args, ("remote-modify", "flathub", "--url=" + original))

    def test_failed_first_switch_retains_official_bootstrap_without_claiming_an_old_source(self):
        official = "https://dl.flathub.org/repo"
        with patch.object(helper, "probe_flathub"), patch.object(helper, "current_flathub", side_effect=["", "https://wrong.example/repo", official]), patch.object(helper, "_flatpak") as run:
            with self.assertRaisesRegex(RuntimeError, "official Flathub remains configured"):
                helper.switch_flathub("sjtu")
        self.assertEqual(run.call_args_list[0].args[0], "remote-add")
        self.assertEqual(run.call_args_list[-1].args, ("remote-modify", "flathub", "--url=" + official))
        self.assertNotIn("remote-delete", str(run.call_args_list))

    @staticmethod
    def mirror_files(architecture="amd64", payload=b"x" * 131072):
        """A current, content-addressed AppStream object for a mirror scenario."""
        import gzip
        import hashlib
        from gi.repository import GLib
        arch = {"amd64": "x86_64", "arm64": "aarch64"}[architecture]
        files = {"config": b"[core]\ncollection-id=org.flathub.Stable\n"}

        def store(value, signature, suffix):
            data = GLib.Variant(signature, value).get_data_as_bytes().get_data()
            checksum = hashlib.sha256(data).hexdigest()
            files[f"objects/{checksum[:2]}/{checksum[2:]}.{suffix}"] = data
            return list(bytes.fromhex(checksum))

        digest = list(hashlib.sha256(payload).digest())
        sample = f"objects/{bytes(digest).hex()[:2]}/{bytes(digest).hex()[2:]}.filez"
        files[sample] = payload
        tree = store(([("appstream.xml", digest)], []), "(a(say)a(sayay))", "dirtree")
        commit = store(({}, [], [], "AppStream", "", 0, tree, [0] * 32), "(a{sv}aya(say)sstayay)", "commit")
        data = GLib.Variant("(a(s(taya{sv}))a{sv})", ([(f"appstream2/{arch}", (0, commit, {}))], {})).get_data_as_bytes().get_data()
        summary = hashlib.sha256(data).digest()
        files[f"summaries/{summary.hex()}.gz"] = gzip.compress(data)
        files["summary.idx"] = GLib.Variant("(a{s(ayaaya{sv})}a{sv})", ({arch: (list(summary), [], {})}, {})).get_data_as_bytes().get_data()
        return files, sample

    def test_speed_measurement_resolves_live_architecture_objects_and_tests_same_file(self):
        from anduinos_control_panel.software_sources import measure_flathub_mirrors
        for architecture in ["amd64", "arm64"]:
            with self.subTest(architecture=architecture):
                files, sample = self.mirror_files(architecture)
                requested = []

                def opener(request, timeout):
                    requested.append(request.full_url)
                    path = request.full_url.split("/repo/", 1)[1]
                    return FakeResponse(files[path])

                measurements = measure_flathub_mirrors(architecture, candidates=("https://one.example/repo", "https://two.example/repo"), opener=opener, clock=AdvancingClock())
                self.assertEqual(len(measurements), 2)
                self.assertTrue(all(item.bandwidth_mbps > 0 for item in measurements))
                self.assertIn("https://one.example/repo/" + sample, requested)
                self.assertIn("https://two.example/repo/" + sample, requested)

    def test_custom_speed_test_only_queries_the_selected_repository(self):
        from anduinos_control_panel.software_sources import measure_flathub_mirrors
        files, _sample = self.mirror_files()
        requested = []

        def opener(request, timeout):
            requested.append(request.full_url)
            return FakeResponse(files[request.full_url.split("/repo/", 1)[1]])

        result = measure_flathub_mirrors("amd64", candidates=("https://custom.example/repo/",), opener=opener, clock=AdvancingClock())
        self.assertEqual(result[0].uri, "https://custom.example/repo")
        self.assertTrue(all(uri.startswith("https://custom.example/repo/") for uri in requested))

    def test_wrong_repository_or_architecture_is_rejected_before_sample_download(self):
        from anduinos_control_panel.software_sources import probe_flathub
        files, _sample = self.mirror_files("arm64")

        def opener(request, timeout):
            return FakeResponse(files[request.full_url.split("/repo/", 1)[1]])

        with self.assertRaisesRegex(ValueError, "current architecture"):
            probe_flathub("https://custom.example/repo", "amd64", opener)
        files["config"] = b"[core]\ncollection-id=another.repository\n"
        with self.assertRaisesRegex(ValueError, "not a Flathub"):
            probe_flathub("https://custom.example/repo", "arm64", opener)

    def test_corrupt_metadata_and_failed_payload_never_produce_a_fastest_mirror(self):
        from anduinos_control_panel.software_sources import measure_flathub_mirrors
        for failure in ["checksum", "truncated", "compression", "payload"]:
            with self.subTest(failure=failure):
                files, sample = self.mirror_files()
                if failure != "payload":
                    key = next(key for key in files if key.startswith("summaries/"))
                    import gzip
                    if failure == "checksum":
                        files[key] = gzip.compress(b"wrong summary")
                    elif failure == "truncated":
                        files[key] = files[key][:6]
                    else:
                        damaged = bytearray(files[key])
                        damaged[10] ^= 0xFF
                        files[key] = bytes(damaged)

                def opener(request, timeout):
                    path = request.full_url.split("/repo/", 1)[1]
                    if path == sample:
                        raise OSError("download offline")
                    return FakeResponse(files[path])

                with self.assertRaises(RuntimeError):
                    measure_flathub_mirrors("amd64", candidates=("https://custom.example/repo",), opener=opener, clock=AdvancingClock())

    def test_read_is_system_only_includes_disabled_and_preserves_custom_url(self):
        from unittest.mock import Mock
        from anduinos_control_panel.software_sources import current_flathub
        runner = Mock(return_value=subprocess.CompletedProcess([], 0,
            'other\thttps://other.example\nflathub\thttps://custom.example/repo/\n', ''))
        self.assertEqual(current_flathub(runner), 'https://custom.example/repo/')
        command = runner.call_args.args[0]
        self.assertIn('--system', command)
        self.assertIn('--show-disabled', command)
        runner.return_value = subprocess.CompletedProcess([], 1, '', 'read failed')
        with self.assertRaisesRegex(RuntimeError, 'read failed'):
            current_flathub(runner)
        runner.return_value = subprocess.CompletedProcess([], 0, 'other\thttps://other.example\n', '')
        self.assertEqual(current_flathub(runner), '')

    def test_invalid_target_is_rejected_before_any_command(self):
        with patch.object(helper, '_flatpak') as run, patch.object(helper, 'current_flathub') as read:
            for value in ['file:///tmp/repo', '--no-gpg-verify', 'user', '', 'https://user:password@example/repo']:
                with self.assertRaises(ValueError):
                    helper.switch_flathub(value)
            run.assert_not_called()
            read.assert_not_called()

    def test_new_remote_imports_official_descriptor_before_switch(self):
        from anduinos_control_panel.software_sources import FLATHUB_SOURCES, FLATHUB_OFFICIAL_REPO
        with patch.object(helper, 'probe_flathub'), patch.object(helper, 'current_flathub', side_effect=['', FLATHUB_SOURCES['official']]), patch.object(helper, '_flatpak') as run:
            helper.switch_flathub('official')
        self.assertEqual(run.call_args_list[0].args,
            ('remote-add', '--if-not-exists', 'flathub', FLATHUB_OFFICIAL_REPO))
        self.assertEqual(run.call_args_list[1].args,
            ('remote-modify', 'flathub', '--enable', '--gpg-verify', '--url=' + FLATHUB_SOURCES['official']))
        self.assertNotIn('remote-delete', str(run.call_args_list))
        self.assertNotIn('no-gpg-verify', str(run.call_args_list))

    def test_unreachable_ustc_does_not_modify_remote(self):
        with patch.object(helper, 'probe_flathub', side_effect=OSError('offline')), patch.object(helper, '_flatpak') as run:
            with self.assertRaises(OSError):
                helper.switch_flathub('ustc')
            run.assert_not_called()

    def test_catalog_failure_does_not_report_completed_switch_as_failure(self):
        from anduinos_control_panel.software_sources import FLATHUB_SOURCES
        with patch.object(helper, 'current_flathub', return_value=FLATHUB_SOURCES['ustc']), patch.object(helper, 'probe_flathub') as probe, patch.object(helper, '_flatpak', side_effect=[None, RuntimeError('catalog offline')]) as run:
            helper.switch_flathub('ustc')
        probe.assert_called_once_with(FLATHUB_SOURCES['ustc'])
        self.assertEqual(run.call_args_list[0].args,
            ('remote-modify', 'flathub', '--enable', '--gpg-verify', '--url=' + FLATHUB_SOURCES['ustc']))

    def test_flatpak_command_cannot_touch_user_installation_or_apt(self):
        with patch.object(helper.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            helper._flatpak('remote-modify', 'flathub', '--enable', '--url=https://dl.flathub.org/repo')
        self.assertEqual(run.call_args.args[0][:2], ['/usr/bin/flatpak', '--system'])
