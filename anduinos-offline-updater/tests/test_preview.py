"""Real signatures, Debian packages and native APT; never installs packages."""
import copy
import gzip
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anduinos_offline_updater.bundle import verify
from anduinos_offline_updater.cli import main, running_version
from anduinos_offline_updater import VERSION
from anduinos_offline_updater.manifest import read_json
from anduinos_offline_updater.native import BundleError, run
from anduinos_offline_updater.planning import preview, snapshot_plan


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="offline-client-tests-")
        cls.root = Path(cls.temp.name)
        cls.signing = cls.root / "signing"; cls.signing.mkdir(mode=0o700)
        cls.keyring = cls.root / "trusted.gpg"
        run(["gpg", "--homedir", str(cls.signing), "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
             "--quick-generate-key", "offline-test@example.invalid", "ed25519", "sign", "1d"])
        run(["gpg", "--homedir", str(cls.signing), "--output", str(cls.keyring), "--export"])
        cls.contents = cls.root / "contents"; (cls.contents / "repository/pool").mkdir(parents=True)
        packages, index = [], []
        for name, ver, depends in [("base-files", "2.0.5-1", "base-library (>= 5)"), ("base-library", "5", None),
                                  ("vim-common", "5", None), ("anduinos-offline-updater", "2.1.0-2", "bootstrap-library (>= 2)"),
                                  ("bootstrap-library", "2", None)]:
            directory = cls.root / name; (directory / "DEBIAN").mkdir(parents=True)
            (directory / "DEBIAN").chmod(0o755)
            control = f"Package: {name}\nVersion: {ver}\nArchitecture: all\nInstalled-Size: 16\nMaintainer: Test <test@example.invalid>\nDescription: Offline test\n"
            if depends: control += f"Depends: {depends}\n"
            (directory / "DEBIAN/control").write_text(control)
            output = cls.root / f"{name}.deb"
            run(["dpkg-deb", "--build", "--root-owner-group", str(directory), str(output)])
            data = output.read_bytes(); sha = hashlib.sha256(data).hexdigest(); path = f"repository/pool/{sha}.deb"
            (cls.contents / path).write_bytes(data)
            packages.append({"identity": {"name": name, "version": ver, "architecture": "all"}, "path": path,
                             "content": {"size": len(data), "sha256": sha}})
            index.append(control + f"Filename: pool/{sha}.deb\nSize: {len(data)}\nSHA256: {sha}\n")
        index_data = "\n".join(index).encode(); (cls.contents / "repository/Packages").write_bytes(index_data)
        release = cls.contents / "repository/Release"
        release.write_text("Origin: AnduinOS Offline\nSuite: resolute\nCodename: resolute\nArchitectures: amd64 all\nDate: Wed, 11 Nov 2026 00:00:00 +0000\nSHA256:\n " +
                           f"{hashlib.sha256(index_data).hexdigest()} {len(index_data)} Packages\n")
        cls.sign(release, cls.contents / "repository/Release.gpg")
        cls.sign(release, cls.contents / "repository/InRelease", clear=True)
        files = {}
        for file in (cls.contents / "repository").rglob("*"):
            if file.is_file():
                data = file.read_bytes(); files[str(file.relative_to(cls.contents))] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        cls.manifest = {"format_version": 1, "product": "AnduinOS", "release_line": "2.0", "suite": "resolute", "architecture": "amd64",
            "target_version": "2.0.5", "batch_id": "a" * 64, "created_utc": "2026-11-11T00:00:00Z",
            "required_features": ["apt-repository-v1", "sequential-apt-plans-v1"], "version_package": "base-files",
            "version_file": "usr/lib/os-release", "stages": [{"operation": "dist-upgrade", "packages": []}],
            "allowed_removals": [], "baselines": [{"name": "test"}], "packages": packages,
            "required_installed_targets": [p["identity"] for p in packages], "source_indexes": {}, "files": files}
        cls.bundle = cls.pack(cls.manifest, "good")

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    @classmethod
    def sign(cls, file, output, clear=False):
        run(["gpg", "--homedir", str(cls.signing), "--batch", "--yes", "--armor", "--output", str(output),
             "--clearsign" if clear else "--detach-sign", str(file)])

    @classmethod
    def pack(cls, m, name, mutation=None):
        raw = cls.root / f"{name}.json"; raw.write_text(json.dumps(m))
        sig = cls.root / f"{name}.asc"; cls.sign(raw, sig)
        contents = {"manifest.json": raw.read_bytes(), "manifest.asc": sig.read_bytes()}
        contents.update({p: (cls.contents / p).read_bytes() for p in m["files"]})
        if mutation: contents = mutation(contents)
        file = cls.root / f"{name}.atgz"
        with tarfile.open(file, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for path, data in contents.items():
                entry = tarfile.TarInfo(path); entry.size = len(data); archive.addfile(entry, io.BytesIO(data))
        return file

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix="case-", dir=self.root); self.work = Path(self.case.name)
        self.system = self.work / "system"; (self.system / "usr/lib").mkdir(parents=True)
        (self.system / "var/lib/dpkg").mkdir(parents=True); self.state("2.0.0", "1")

    def tearDown(self): self.case.cleanup()

    @staticmethod
    def package(name, version, depends="", hold=False):
        return f"Package: {name}\nArchitecture: all\nVersion: {version}\nStatus: {'hold' if hold else 'install'} ok installed\nInstalled-Size: 16\nDescription: Test\n" + (f"Depends: {depends}\n" if depends else "") + "\n"

    def state(self, version, lib, extra="", hold=False):
        (self.system / "usr/lib/os-release").write_text(f"ID=anduinos\nVERSION_ID={version}\nUBUNTU_CODENAME=resolute\n")
        (self.system / "var/lib/dpkg/status").write_text(self.package("base-files", version + "-1", "base-library (>= 5)" if version == "2.0.5" else "base-library (>= 1)") +
            self.package("base-library", lib, hold=hold) + self.package("vim-common", lib) + extra)

    def inspect(self, file=None, client_version="2.1.0-1"):
        with tempfile.TemporaryDirectory(dir=self.work) as path:
            v = verify(file or self.bundle, self.keyring, path)
            before = (self.system / "var/lib/dpkg/status").read_bytes()
            result = preview(v["manifest"], Path(path) / "repository", self.keyring, self.system, self.work, client_version=client_version)
            self.assertEqual(before, (self.system / "var/lib/dpkg/status").read_bytes()); return result

    def test_old_intermediate_current_and_user_apps(self):
        for version, lib in [("2.0.0", "1"), ("2.0.3", "3"), ("2.0.5", "5")]:
            self.state(version, lib, self.package("docker-ce", "29.9.3")); result = self.inspect()
            self.assertEqual(result["state"], "already-installed" if lib == "5" else "update-available", result)
            self.assertFalse(any(c["name"] == "docker-ce" for s in result["stages"] for c in s["changes"]))
            self.assertFalse(result["can_install"])

    def test_held_and_strict_vim_dependencies_block(self):
        self.state("2.0.0", "1", hold=True); self.assertEqual(self.inspect()["state"], "blocked")
        self.state("2.0.0", "1", self.package("vim", "1", "vim-common (= 1)")); result = self.inspect()
        self.assertEqual(result["state"], "blocked", result); self.assertIn("vim", str(result["blockers"]))

    def test_architecture_transition_does_not_skip_a_held_target(self):
        status = self.system / "var/lib/dpkg/status"
        original = status.read_text().replace("Architecture: all", "Architecture: amd64", 1)
        status.write_text(original)
        self.assertEqual(self.inspect()["state"], "update-available")
        status.write_text(original.replace("Status: install ok installed", "Status: hold ok installed", 1))
        result = self.inspect()
        self.assertEqual(result["state"], "blocked", result)
        self.assertIn("base-files", str(result["blockers"]))

    def test_preferences_are_preserved_and_hooks_never_run(self):
        directory = self.system / "etc/apt/preferences.d"; directory.mkdir(parents=True)
        (directory / "hold-library").write_text("Package: base-library\nPin: version 5\nPin-Priority: -1\n")
        marker = self.work / "hook-ran"; conf = self.work / "hostile.conf"
        conf.write_text(f'APT::Update::Post-Invoke {{ "touch {marker}"; }};\n')
        import apt_pkg
        apt_pkg.config.set("APT::Update::Post-Invoke::", f"touch {marker}")
        with patch.dict(os.environ, {"APT_CONFIG": str(conf)}): result = self.inspect()
        self.assertEqual(result["state"], "blocked", result); self.assertFalse(marker.exists())

    def test_bootstrap_only_then_unknown_installer_is_blocked(self):
        m = copy.deepcopy(self.manifest); m["required_features"].append("updater-bootstrap-v1")
        m["bootstrap"] = {"schema_version": 1, "package": "anduinos-offline-updater", "minimum_version": "2.1.0-2"}
        m["required_installer_features"] = ["future-installer-v2"]; file = self.pack(m, "bootstrap")
        result = self.inspect(file); self.assertEqual(result["state"], "updater-update-required", result)
        self.assertEqual({c["name"] for s in result["stages"] for c in s["changes"]}, {"anduinos-offline-updater", "bootstrap-library"})
        self.assertEqual(self.inspect(file, "2.1.0-2")["state"], "blocked")
        m["bootstrap"]["minimum_version"] = "99"
        with tempfile.TemporaryDirectory(dir=self.work) as out:
            with self.assertRaises(BundleError): verify(self.pack(m, "bad-minimum"), self.keyring, out)

    def test_newer_os_refused_and_newer_packages_kept(self):
        self.state("2.0.6", "6")
        with self.assertRaises(BundleError): self.inspect()
        self.state("2.0.0", "6"); result = self.inspect()
        self.assertEqual(result["state"], "update-available", result)
        self.assertFalse(any(c["name"] == "base-library" for s in result["stages"] for c in s["changes"]))

    def test_bad_signature_hash_missing_extra_and_traversal(self):
        def corrupt(v):
            k = self.manifest["packages"][0]["path"]; v[k] = v[k][:-1] + bytes([v[k][-1] ^ 1]); return v
        def missing(v): v.pop(self.manifest["packages"][0]["path"]); return v
        def signature(v): v["manifest.asc"] = b"not a signature"; return v
        def traversal(v): v["../escaped"] = b"bad"; return v
        for i, mutation in enumerate((corrupt, missing, signature, traversal)):
            with tempfile.TemporaryDirectory(dir=self.work) as out:
                with self.assertRaises(BundleError): verify(self.pack(self.manifest, f"bad-{i}", mutation), self.keyring, out)
        self.assertFalse((self.work / "escaped").exists())

    def test_unknown_envelope_duplicate_json_and_explicit_null(self):
        m = copy.deepcopy(self.manifest); m["required_features"].append("unknown-envelope")
        with tempfile.TemporaryDirectory(dir=self.work) as out:
            with self.assertRaises(BundleError): verify(self.pack(m, "unknown"), self.keyring, out)
        for data in (b'{"x":1,"x":2}', b'{"x":null}', b'{"x":NaN}'):
            with self.assertRaises(BundleError): read_json(data)

    def test_archive_links_duplicates_extended_headers_and_trailer(self):
        for case in ("link", "duplicate", "oversized-pax", "sparse", "trailer"):
            file = self.work / f"{case}.atgz"
            if case == "trailer":
                file.write_bytes(gzip.compress(gzip.decompress(self.bundle.read_bytes()) + b"unexpected"))
            else:
                with tarfile.open(self.bundle, "r:gz") as source, tarfile.open(file, "w:gz", format=tarfile.PAX_FORMAT) as output:
                    for entry in source:
                        data = source.extractfile(entry).read()
                        if entry.name.startswith("repository/pool/"):
                            if case == "link":
                                entry.type = tarfile.SYMTYPE; entry.linkname = "/etc/passwd"; entry.size = 0
                            elif case == "oversized-pax":
                                entry.pax_headers = {"comment": "x" * 65536}
                            elif case == "sparse":
                                entry.pax_headers = {"GNU.sparse.map": "0,1"}
                        output.addfile(entry, io.BytesIO(data))
                        if case == "duplicate" and entry.name.startswith("repository/pool/"):
                            output.addfile(entry, io.BytesIO(data))
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.work) as out:
                with self.assertRaises(BundleError): verify(file, self.keyring, out)

    def test_cli_reports_missing_trust_as_json_without_traceback(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            code = main(["verify", str(self.bundle), "--keyring", str(self.work / "missing.gpg"), "--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["state"], "blocked")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["can_install"])

    def test_snapshot_policy_uses_the_existing_apt_table(self):
        self.assertEqual(snapshot_plan(self.system)["state"], "unknown")
        with patch("pathlib.Path.is_file", return_value=False):
            self.assertEqual(snapshot_plan(Path("/"))["state"], "unavailable")
        cases = [("[apt]\nsnapshot_before = false\n", b"btrfs\n", "disabled"),
                 ("[apt]\nsnapshot_before = true\n", b"ext4\n", "unavailable"),
                 ("[apt]\nsnapshot_before = true\n", b"btrfs\n", "requires-layout-check"),
                 ("snapshot_before = false\n", b"btrfs\n", "unknown")]
        for policy, filesystem, state in cases:
            with self.subTest(state=state), patch("pathlib.Path.is_file", return_value=True), \
                    patch("pathlib.Path.read_text", return_value=policy), \
                    patch("anduinos_offline_updater.planning.run", return_value=filesystem):
                self.assertEqual(snapshot_plan(Path("/"))["state"], state)

    def test_running_version_retains_suite_suffix_but_not_new_unloaded_code(self):
        with patch("anduinos_offline_updater.cli.run", return_value=(VERSION + "+resolute").encode()):
            self.assertEqual(running_version(), VERSION + "+resolute")
        with patch("anduinos_offline_updater.cli.run", return_value=b"99.0.0-1+resolute"):
            self.assertEqual(running_version(), VERSION)
        with patch("anduinos_offline_updater.cli.run", side_effect=BundleError("Not installed")):
            self.assertEqual(running_version(), VERSION)

    def test_unfinished_dpkg_wrong_suite_and_low_space(self):
        p = self.system / "var/lib/dpkg/status"; p.write_text(p.read_text().replace("ok installed", "ok unpacked", 1))
        self.assertEqual(self.inspect()["state"], "blocked"); self.state("2.0.0", "1")
        (self.system / "usr/lib/os-release").write_text("ID=anduinos\nVERSION_ID=2.0.0\nUBUNTU_CODENAME=wrong\n")
        with self.assertRaises(BundleError): self.inspect()
        with tempfile.TemporaryDirectory(dir=self.work) as out:
            with patch("anduinos_offline_updater.bundle.shutil.disk_usage", return_value=shutil._ntuple_diskusage(1, 1, 0)):
                with self.assertRaisesRegex(BundleError, "Insufficient space"): verify(self.bundle, self.keyring, out)
            self.assertFalse(any(Path(out).iterdir()))


if __name__ == "__main__": unittest.main()
