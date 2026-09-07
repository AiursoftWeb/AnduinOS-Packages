"""Exercise the real APT solver with a disposable local package universe.

Never reads host package state, downloads packages or executes maintainer scripts.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class SnapPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anduinos-snap-policy.")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for directory in ("repo", "lists/partial", "archives/partial", "preferences.d", "apt.conf.d", "sources.list.d"):
            (self.path / directory).mkdir(parents=True)
        project = ET.parse(ROOT / "anduinos-no-snapd.aosproj").getroot()
        # External desktop behavior is a solver fixture, not a sibling source read.
        # The real recommendation is checked by the desktop package itself.
        self.desktop_version = "2.0.2-3"
        self.blocker_version = project.findtext(".//PackageVersion").split("+")[0]
        self.assertEqual(project.findtext(".//Conflicts"), "snapd")
        self.assertFalse(project.findall(".//PostInstallScript"))
        self.assertFalse((ROOT / "scripts/postinst.sh").exists())
        self.fields = {
            "anduinos-desktop": "Depends: desktop-core\nRecommends: anduinos-no-snapd (>= 2.0.2-2)\n",
            "desktop-core": "",
            "anduinos-no-snapd": "Conflicts: snapd\nReplaces: snapd\n",
            "snapd": "",
            "snap-only-app": "Depends: snapd\n",
        }
        self.versions = {name: "1" for name in self.fields}
        self.versions.update({"anduinos-desktop": self.desktop_version,
                              "anduinos-no-snapd": self.blocker_version})
        packages = "".join(self.record(name) + f"Filename: {name}.deb\nSize: 1\n\n"
                           for name in self.fields)
        (self.path / "repo/Packages").write_text(packages)
        (self.path / "sources.list").write_text(f"deb [trusted=yes] file:{self.path}/repo ./\n")
        (self.path / "preferences").write_text("")
        (self.path / "status").write_text("")
        config = f'''Dir::Etc::main "/dev/null";
Dir::Etc::parts "{self.path}/apt.conf.d";
Dir::Etc::sourcelist "{self.path}/sources.list";
Dir::Etc::sourceparts "{self.path}/sources.list.d";
Dir::Etc::preferences "{self.path}/preferences";
Dir::Etc::preferencesparts "{self.path}/preferences.d";
Dir::State::status "{self.path}/status";
Dir::State::extended_states "{self.path}/extended_states";
Dir::State::lists "{self.path}/lists";
Dir::Cache::archives "{self.path}/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Log "{self.path}";
APT::Architecture "amd64";
APT::Architectures {{ "amd64"; }};
APT::Install-Recommends "true";
Acquire::Languages "none";
'''
        (self.path / "apt.conf").write_text(config)
        self.env = {**os.environ, "APT_CONFIG": str(self.path / "apt.conf"), "LC_ALL": "C"}
        result = self.apt("update", simulate=False)
        self.assertEqual(result.returncode, 0, result.stdout)

    def record(self, name, installed=False, legacy=False):
        fields = self.fields[name]
        if legacy and name == "anduinos-desktop":
            fields = "Depends: desktop-core, anduinos-no-snapd\n"
        return (f"Package: {name}\nVersion: {self.versions[name]}\nArchitecture: amd64\n"
                + ("Status: install ok installed\n" if installed else "")
                + "Maintainer: Test <test@example.invalid>\nDescription: Isolated solver fixture\n" + fields)

    def installed(self, names, legacy=False):
        (self.path / "status").write_text("\n".join(self.record(n, True, legacy) for n in names))

    def pin(self):
        (self.path / "preferences.d/no-snap.pref").write_text((ROOT / "assets/no-snap.pref").read_text())

    def apt(self, *args, simulate=True):
        return subprocess.run(["apt-get", *( ["--simulate"] if simulate else []), *args],
                              env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=30)

    def test_fresh_desktop_recommends_blocker(self):
        result = self.apt("install", "anduinos-desktop")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Inst anduinos-no-snapd", result.stdout)

    def test_default_blocks_direct_and_transitive_snap_install(self):
        self.installed(["anduinos-desktop", "desktop-core", "anduinos-no-snapd"])
        self.pin()
        for name in ("snapd", "snap-only-app"):
            result = self.apt("install", name)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("Remv anduinos-desktop", result.stdout)

    def test_explicit_opt_out_keeps_desktop(self):
        self.installed(["anduinos-desktop", "desktop-core", "anduinos-no-snapd"])
        self.pin()
        result = self.apt("install", "anduinos-desktop", "anduinos-no-snapd-")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Remv anduinos-no-snapd", result.stdout)
        self.assertNotIn("Remv anduinos-desktop", result.stdout)
        self.assertNotIn("Remv desktop-core", result.stdout)

    def test_old_desktop_refuses_opt_out_instead_of_removing_desktop(self):
        self.installed(["anduinos-desktop", "desktop-core", "anduinos-no-snapd"], legacy=True)
        # Keep the legacy record in the repository too: no newer package to upgrade to.
        packages = "".join(self.record(n, legacy=True) + f"Filename: {n}.deb\nSize: 1\n\n"
                           for n in self.fields)
        (self.path / "repo/Packages").write_text(packages)
        self.assertEqual(self.apt("update", simulate=False).returncode, 0)
        result = self.apt("install", "anduinos-desktop", "anduinos-no-snapd-")
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_install_snap_after_opt_out_needs_no_removals(self):
        self.installed(["anduinos-desktop", "desktop-core"])
        result = self.apt("--no-remove", "install", "snapd")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Inst snapd", result.stdout)
        self.assertNotIn("Remv ", result.stdout)
        self.assertNotIn("Inst anduinos-no-snapd", result.stdout)

    def test_no_remove_guard_rejects_conflicting_install(self):
        self.installed(["anduinos-desktop", "desktop-core", "anduinos-no-snapd"])
        result = self.apt("--no-remove", "install", "snapd")
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_upgrade_does_not_reintroduce_blocker_for_snap_user(self):
        self.installed(["anduinos-desktop", "desktop-core", "snapd"])
        status = self.path / "status"
        status.write_text(status.read_text().replace(
            f"Package: anduinos-desktop\nVersion: {self.desktop_version}\n",
            "Package: anduinos-desktop\nVersion: 2.0.2-2\n"))
        result = self.apt("--no-remove", "install", "anduinos-desktop")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Inst anduinos-desktop", result.stdout)
        self.assertNotIn("Inst anduinos-no-snapd", result.stdout)
        self.assertNotIn("Remv ", result.stdout)
