"""The installed notice must retain actual upstream texts and reject omissions."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rust-notices.py"
spec = importlib.util.spec_from_file_location("rust_notices", SCRIPT)
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class RustNoticesTests(unittest.TestCase):
    def collect(self, root, packages, target="x86_64-unknown-linux-gnu"):
        standard = root / "stdlib-notice"
        standard.write_text("Real toolchain attribution")
        output = root / "notices"
        metadata = {"packages": packages,
                    "resolve": {"nodes": [{"id": p["id"]} for p in packages]}}
        with patch.dict(os.environ, {"RUST_STDLIB_NOTICES": str(standard)}), \
                patch.object(notices.sys, "argv", [str(SCRIPT), target, str(output)]), \
                patch.object(notices.subprocess, "check_output", side_effect=[
                    json.dumps(metadata), "rustc 1.99.0", str(root)]) as command:
            notices.main()
        self.assertIn("--filter-platform", command.call_args_list[0].args[0])
        self.assertIn(target, command.call_args_list[0].args[0])
        return output.read_text()

    def dependency(self, root, name, spelling):
        folder = root / name
        folder.mkdir()
        (folder / spelling).write_text(f"Upstream permission text for {name}")
        (folder / "NOTICE").write_text(f"Original copyright for {name}")
        return {"id": name, "name": name, "version": "1.0", "source": "registry+test",
                "manifest_path": str(folder / "Cargo.toml"), "license": "MIT",
                "license_file": None, "repository": "https://example.org/" + name}

    def test_retains_licence_spelling_notices_and_toolchain_text_on_both_targets(self):
        for target in ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                packages = [self.dependency(root, "gtk", "LICENSE"),
                            self.dependency(root, "adw", "LICENCE")]
                text = self.collect(root, packages, target)
                self.assertIn("Real toolchain attribution", text)
                for name in ("gtk", "adw"):
                    self.assertIn(f"Upstream permission text for {name}", text)
                    self.assertIn(f"Original copyright for {name}", text)
                self.assertIn("not all listed code is linked", text)

    def test_spdx_label_does_not_replace_missing_permission_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.dependency(root, "missing", "LICENSE")
            (root / "missing/LICENSE").unlink()
            with self.assertRaisesRegex(SystemExit, "Missing license text: missing"):
                self.collect(root, [package])
            self.assertFalse((root / "notices").exists())

    def test_declared_nested_license_file_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.dependency(root, "nested", "LICENSE")
            nested = root / "nested/legal"
            nested.mkdir()
            (nested / "terms.txt").write_text("Upstream nested licence")
            (root / "nested/LICENSE").unlink()
            package["license_file"] = "legal/terms.txt"
            self.assertIn("Upstream nested licence", self.collect(root, [package]))

    def test_omitted_workspace_licence_uses_recorded_revision_and_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.dependency(root, "workspace", "LICENSE")
            (root / "workspace/LICENSE").unlink()
            revision = "a" * 40
            package["repository"] = "https://github.com/example/project"
            (root / "workspace/.cargo_vcs_info.json").write_text(json.dumps({
                "git": {"sha1": revision}, "path_in_vcs": "workspace"}))
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                for name, text in {"LICENSE-MIT": "Original MIT permission",
                                   "workspace/NOTICE": "Original attribution",
                                   "other/LICENSE": "Unrelated package"}.items():
                    data = text.encode()
                    member = tarfile.TarInfo("source/" + name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            with patch.object(notices.urllib.request, "urlopen",
                              return_value=io.BytesIO(buffer.getvalue())) as request:
                text = self.collect(root, [package])
            self.assertIn("Original MIT permission", text)
            self.assertIn("Original attribution", text)
            self.assertNotIn("Unrelated package", text)
            self.assertEqual(request.call_args.args[0],
                             "https://codeload.github.com/example/project/tar.gz/" + revision)
            with patch.object(notices.urllib.request, "urlopen") as request:
                self.assertEqual(self.collect(root, [package]), text)
                request.assert_not_called()
