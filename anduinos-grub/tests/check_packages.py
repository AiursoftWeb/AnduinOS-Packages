"""Read-only phase-one checks. Run after building BOTH boot packages with --all.

Unlike the source unit tests, this command requires real .deb artifacts and
verified apkg inputs. It never installs a package or runs a maintainer script.
"""
import importlib.util
import io
import json
from pathlib import Path
import re
import struct
import subprocess
import tarfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("assembly", ROOT / "assemble-efi.py")
assembly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assembly)


def targets(project_dir):
    project = ET.parse(next(project_dir.glob("*.aosproj")))
    prop = lambda name: project.findtext(".//" + name)
    mappings = dict(item.split("=", 1) for item in prop("SuiteShortNameMap").split())
    for suite in prop("TargetSuites").split():
        version = prop("PackageVersion").replace("$(SuiteShortName)", mappings[suite]).replace("$(Suite)", suite)
        for arch in prop("TargetArchitectures").split():
            deb = project_dir / "bin" / f"{prop('PackageName')}_{version}_{suite}_{arch}.deb"
            inputs = project_dir / "obj/inputs" / prop("TargetDistro") / suite / arch
            yield suite, arch, deb, inputs


def payload(deb):
    data = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(deb)])
    archive = tarfile.open(fileobj=io.BytesIO(data))
    files = {item.name.removeprefix("./"): item for item in archive.getmembers()}
    return archive, files


def read(archive, files, name):
    return archive.extractfile(files[name]).read()


class PackageTests(unittest.TestCase):
    def test_grub_artifacts(self):
        for suite, arch, deb, inputs in targets(ROOT):
            with self.subTest(suite=suite, arch=arch):
                archive, files = payload(deb)
                suffix, platform, pe_machine, elf_machine = {
                    "amd64": ("x64", "x86_64-efi", 0x8664, 62),
                    "arm64": ("aa64", "arm64-efi", 0xAA64, 183),
                }[arch]
                self.assertEqual(subprocess.check_output(["dpkg-deb", "-f", str(deb), "Architecture"], text=True).strip(), arch)
                for field in ("Provides", "Conflicts", "Replaces"):
                    value = subprocess.check_output(["dpkg-deb", "-f", str(deb), field], text=True)
                    self.assertIn(f"grub-efi-{arch}", value)
                    self.assertNotIn("grub-efi-" + ("arm64" if arch == "amd64" else "amd64"), value)
                    self.assertEqual("grub-pc-bin" in value, arch == "amd64")
                image = read(archive, files, f"usr/lib/anduinos-grub/grub{suffix}.efi")
                machine, sections = assembly.pe_sections(image)  # Also rejects signatures.
                self.assertEqual(machine, pe_machine)
                objects = assembly.grub_objects(sections["mods"])
                names = {assembly.elf_module_name(data) for kind, data in objects if kind == 0}
                self.assertTrue({"btrfs", "luks2", "argon2", "peimage", "linux"} <= names)
                doc = "usr/share/doc/anduinos-grub/"
                self.assertTrue(read(archive, files, doc + "copyright"))
                self.assertEqual(names, set(read(archive, files, doc + "modules.txt").decode().splitlines()))
                self.assertEqual(sections[".sbat"].rstrip(b"\0"), read(archive, files, doc + "sbat.csv"))
                self.assertEqual(read(archive, files, doc + "manifest.json"), (inputs / "manifest.json").read_bytes())
                self.assertEqual(any(name.startswith("usr/lib/grub/i386-pc/") for name in files), arch == "amd64")
                for name in ("usr/bin/grub-mkimage", "usr/sbin/grub-install", "usr/sbin/grub-probe"):
                    data = read(archive, files, name)
                    self.assertEqual(struct.unpack_from("<H", data, 18)[0], elf_machine)
                    self.assertEqual(data, (inputs / "extracted" / ("tools-" + arch) / name).read_bytes())
                    self.assertTrue(files[name].mode & 0o111)
                for name in ("etc/kernel/postinst.d/zz-update-grub", "etc/kernel/postrm.d/zz-update-grub", "etc/grub.d/05_debian_theme"):
                    self.assertTrue(files[name].mode & 0o111)
                self.assertNotIn("etc/default/grub", files)  # Preserve user configuration.
                self.assertFalse(any("/monolithic/" in name for name in files))
                self.assertFalse(any("/lib/shim/" in name for name in files))
                self.assertFalse(any("private" in name.lower() or "MOK.priv" in name for name in files))
                # A new upstream runtime dependency must not silently go missing.
                manifest = json.loads((inputs / "manifest.json").read_text())
                tools = next(item for item in manifest["Inputs"] if item["Id"] == "tools-" + arch)
                required = subprocess.check_output(["dpkg-deb", "-f", str(inputs / tools["DebPath"]), "Depends"], text=True)
                provided = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Depends"], text=True)
                dependency_names = set(re.findall(r"(?:^|,)\s*([a-z0-9+.-]+)", provided))
                for group in required.split(","):
                    alternatives = {part.strip().split()[0] for part in group.split("|")}
                    self.assertTrue(alternatives & dependency_names, f"Missing runtime dependency: {group}")
                archive.close()

    def test_shim_artifacts(self):
        for suite, arch, deb, inputs in targets(ROOT.parent / "anduinos-shim-signed"):
            with self.subTest(suite=suite, arch=arch):
                archive, files = payload(deb)
                suffix = {"amd64": "x64", "arm64": "aa64"}[arch]
                self.assertEqual(subprocess.check_output(["dpkg-deb", "-f", str(deb), "Architecture"], text=True).strip(), arch)
                self.assertIn("anduinos-grub", subprocess.check_output(["dpkg-deb", "-f", str(deb), "Depends"], text=True))
                upstream = inputs / "extracted/shim-signed"
                self.assertTrue(read(archive, files, "usr/share/doc/anduinos-shim-signed/copyright"))
                for filename in (f"shim{suffix}.efi.signed.latest", f"mm{suffix}.efi", "is-not-revoked"):
                    path = "usr/lib/shim/" + filename
                    self.assertEqual(read(archive, files, path), (upstream / path).read_bytes())
                self.assertEqual(read(archive, files, "usr/share/doc/anduinos-shim-signed/manifest.json"), (inputs / "manifest.json").read_bytes())
                self.assertFalse(any(name.startswith("boot/") or name.startswith("var/lib/shim-signed/") for name in files))
                archive.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
