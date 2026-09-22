import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "assets/anduinos-media-check"


class MediaCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.media = self.root / "media"
        self.state = self.root / "state"
        (self.media / "LiveOS").mkdir(parents=True)
        (self.media / ".disk").mkdir()
        self.source = self.media / "LiveOS/rootfs.squashfs"
        for name in ("rootfs.squashfs", "vmlinuz", "initrd", "filesystem.manifest"):
            (self.media / "LiveOS" / name).write_bytes((name.encode() + b"\n") * 1024)
        self.manifest = self.media / "md5sum.txt"
        self.manifest.write_text("".join(
            f"{hashlib.md5(p.read_bytes()).hexdigest()}  ./LiveOS/{p.name}\n"
            for p in sorted((self.media / "LiveOS").iterdir())
        ))

    def run_check(self, *extra):
        result = subprocess.run([
            "bash", str(CHECKER), "--media", str(self.media),
            "--state-dir", str(self.state), *map(str, extra),
        ], capture_output=True, text=True, timeout=20)
        return result

    def report(self):
        return dict(line.split("=", 1) for line in
                    (self.state / "media-check.result").read_text().splitlines())

    def test_good_files_have_real_completed_result_and_are_reused(self):
        first = self.run_check()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.report()["status"], "passed")
        self.assertEqual(self.report()["decision"], "none")
        self.assertIn("ANDUINOS_MEDIA_PROGRESS=100", first.stdout)
        second = self.run_check()
        self.assertEqual(second.returncode, 0)
        self.assertNotIn("ANDUINOS_MEDIA_PROGRESS", second.stdout)

    def test_corrupt_source_invalidates_cached_pass(self):
        self.assertEqual(self.run_check().returncode, 0)
        data = self.source.read_bytes()
        self.source.write_bytes(b"!" + data[1:])
        failed = self.run_check()
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertEqual(self.report()["status"], "failed")
        self.assertNotIn("ANDUINOS_MEDIA_PROGRESS=100", failed.stdout)

    def test_source_argument_cannot_substitute_another_image(self):
        other = self.root / "other.squashfs"
        shutil.copyfile(self.source, other)
        result = self.run_check("--source", other)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.report()["status"], "unavailable")
        self.assertEqual(self.report()["reason"], "wrong-source")

    def test_incomplete_manifest_cannot_pass(self):
        self.manifest.write_text("\n".join(
            line for line in self.manifest.read_text().splitlines()
            if not line.endswith("rootfs.squashfs")) + "\n")
        self.assertNotEqual(self.run_check().returncode, 0)
        self.assertEqual(self.report()["status"], "unavailable")

    def test_no_checksums_is_unknown_not_corruption(self):
        self.manifest.unlink()
        self.assertNotEqual(self.run_check().returncode, 0)
        self.assertEqual(self.report()["status"], "unavailable")

    def test_failed_result_is_not_cleared_by_another_invocation(self):
        self.source.write_bytes(b"broken")
        self.assertEqual(self.run_check().returncode, 1)
        self.assertEqual(self.run_check().returncode, 1)
        self.assertEqual(self.report()["status"], "failed")

    def test_explicit_live_continuation_is_reused_only_by_interactive_boot(self):
        self.assertEqual(self.run_check().returncode, 0)
        report = self.state / "media-check.result"
        data = report.read_text().replace("status=passed", "status=failed")
        data = data.replace("decision=none", "decision=continue")
        report.write_text(data)
        boot = self.run_check("--interactive")
        self.assertEqual(boot.returncode, 2, boot.stdout + boot.stderr)
        installer = self.run_check()
        self.assertEqual(installer.returncode, 1)

    def test_different_boot_cannot_reuse_previous_pass(self):
        self.assertEqual(self.run_check().returncode, 0)
        report = self.state / "media-check.result"
        report.write_text(report.read_text().replace("boot_id=", "old_boot_id="))
        self.assertIn("ANDUINOS_MEDIA_PROGRESS", self.run_check().stdout)

    def test_cancelled_report_is_checked_again_before_install(self):
        self.assertEqual(self.run_check().returncode, 0)
        report = self.state / "media-check.result"
        report.write_text(report.read_text().replace("status=passed", "status=skipped"))
        self.assertIn("ANDUINOS_MEDIA_PROGRESS", self.run_check().stdout)
        self.assertEqual(self.report()["status"], "passed")

    def test_symlink_outside_media_is_rejected(self):
        target = self.root / "outside"
        self.source.rename(target)
        self.source.symlink_to(target)
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_manifest_path_cannot_escape_media(self):
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        self.manifest.write_text(
            f"{hashlib.md5(outside.read_bytes()).hexdigest()}  ./LiveOS/../../outside\n"
        )
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.report()["status"], "unavailable")
        self.assertEqual(self.report()["reason"], "invalid-manifest")

    def test_existing_md5_manifest_allows_empty_and_space_named_assets(self):
        asset = self.media / "empty asset"
        asset.touch()
        with self.manifest.open("a") as stream:
            stream.write(f"{hashlib.md5(b'').hexdigest()}  ./empty asset\n")
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.report()["status"], "passed")

    def test_real_iso_engine_passes_clean_and_fails_modified_iso(self):
        if not all(shutil.which(cmd) for cmd in ("xorriso", "implantisomd5", "checkisomd5")):
            self.skipTest("ISO tools unavailable")
        image = self.root / "fixture.iso"
        # isomd5sum's fragment checkpoints require a realistically sized ISO.
        (self.media / "padding").write_bytes(b"x" * (8 * 1024 * 1024))
        subprocess.run(["xorriso", "-as", "mkisofs", "-o", str(image), str(self.media)],
                       check=True, capture_output=True)
        subprocess.run(["implantisomd5", "--force", str(image)], check=True, capture_output=True)
        result = self.run_check("--device", image, "--force")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr +
                         (self.state / "media-check.log").read_text())
        with image.open("r+b") as stream:
            # System area is covered by the embedded checksum but changing it
            # does not prevent the primary ISO descriptor from being parsed.
            stream.seek(10000)
            stream.write(b"damage")
        self.assertEqual(self.run_check("--device", image, "--force").returncode, 1)
        self.assertEqual(self.report()["status"], "failed")

    def test_native_plymouth_progress_and_recovery_preserve_results(self):
        commands = self.root / "commands"
        commands.mkdir()
        client = commands / "plymouth"
        client.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys, time
root = pathlib.Path(os.environ["PLYMOUTH_TEST"])
args = sys.argv[1:]
with (root / "calls").open("a") as f:
    f.write(json.dumps(args) + "\\n")
if args and args[0] == "display-message":
    text = args[1].removeprefix("--text=")
    assert len(text.encode()) < 254, text
    assert not text.startswith(("anduinos-clear:", "anduinos-append:"))
    if text.startswith("keys:") and "[D]" in text:
        (root / "recovery").touch()
if args and args[0] == "watch-keystroke":
    while not (root / "recovery").exists():
        time.sleep(.02)
    counter = root / "key-count"
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    print("d" if n < 2 else "c")
''')
        client.chmod(0o755)
        env = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}",
               "PLYMOUTH_TEST": str(self.root)}
        invocation = ["bash", str(CHECKER), "--media", str(self.media),
                      "--state-dir", str(self.state), "--interactive", "--force"]
        passed = subprocess.run(invocation, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertEqual(self.report()["status"], "passed")
        calls = [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]
        self.assertIn(["update", "--status=fsck:md5sums:0"], calls)
        self.assertIn(["update", "--status=fsck:md5sums:100"], calls)
        self.assertIn(["display-message", "--text=keys:[S] Skip check"], calls)

        self.source.write_bytes(b"broken")
        failed = subprocess.run(invocation, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertEqual(self.report()["status"], "failed")
        self.assertEqual(self.report()["decision"], "continue")
        self.assertGreaterEqual(int((self.root / "key-count").read_text()), 3)
        self.assertEqual(self.run_check().returncode, 1)


class LocaleTests(unittest.TestCase):
    def test_all_28_live_locales_have_complete_messages(self):
        locales = list((ROOT / "data/media-check").glob("*.txt"))
        self.assertEqual(len(locales), 28)
        expected = set(line.split("=", 1)[0] for line in
                       (ROOT / "data/media-check/en_US.txt").read_text().splitlines())
        for path in locales:
            with self.subTest(locale=path.stem):
                data = dict(line.split("=", 1) for line in path.read_text().splitlines())
                self.assertEqual(set(data), expected)
                self.assertTrue(all(data.values()))
                self.assertTrue(all(f"[{key}]" in data["actions"] for key in "DRPC"))

    def test_plymouth_messages_fit_native_protocol(self):
        # The helper wraps only at spaces and keeps each wire command below
        # Plymouth's small payload limit, so no translated word may exceed a
        # complete fragment by itself.
        for path in (ROOT / "data/media-check").glob("*.txt"):
            for line in path.read_text().splitlines():
                _, value = line.split("=", 1)
                self.assertLessEqual(max(map(len, value.encode().split())), 180,
                                     f"unbreakable Plymouth text in {path.name}")
                key = line.split("=", 1)[0]
                if key in {"checking", "passed", "failed", "unavailable", "skipped", "skip", "actions"}:
                    self.assertLess(len(("keys:" + value).encode()), 254, path.name)


class DracutContractTests(unittest.TestCase):
    def test_check_runs_before_upstream_mount_and_disables_legacy_checker(self):
        wrapper = (ROOT / "dracut/95anduinos-live-layers/anduinos-live-root.sh").read_text()
        self.assertLess(wrapper.index("/usr/libexec/anduinos-media-check"),
                        wrapper.index(". /sbin/dmsquash-live-root.upstream"))
        self.assertIn("rd.anduinos.media-check", wrapper)
        self.assertIn("rd.overlay", wrapper)
        self.assertIn("LABEL=ANDUINOS-PERSIST", wrapper)
        self.assertIn("[[ $mode != force ]] || args+=(--force)", wrapper)
        self.assertIn("check_rc != 0 && check_rc != 2", wrapper)
        self.assertIn("rd\\.live\\.check", wrapper)
        self.assertIn("getcmdline()", wrapper)

    def test_initrd_uses_existing_md5_manifest_backend(self):
        module = (ROOT / "dracut/95anduinos-live-layers/module-setup.sh").read_text()
        self.assertIn("checkisomd5 md5sum", module)
        self.assertNotIn("sha256sum", module)
        self.assertIn("dmsquash-live-root.upstream", module)
        self.assertNotIn("anduinos-media.plymouth", module)
        self.assertNotIn("script.so", module)
        self.assertNotIn('"$initdir/usr/share/plymouth/themes/default.plymouth"', module)


if __name__ == "__main__":
    unittest.main()
