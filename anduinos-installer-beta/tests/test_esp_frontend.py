import ast
import contextlib
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from frontend import FrontendPlanError, probe_esp_occupied
from installer_core.esp import EspReuseInspection, EspTreeEntry
from installer_core.manual_layout import ManualPartitionRole
from installer_core.storage_ui import ManualStoragePreview
from storage_probe_cli import main
from test_other_systems import external_windows_disk


class EspFrontendTests(unittest.TestCase):
    def test_identity_and_boolean_are_validated_across_polkit_boundary(self):
        part = external_windows_disk().partitions[0]
        for scenario in ("free", "occupied", "identity", "uuid", "type", "json", "failed"):
            with self.subTest(scenario=scenario), patch("frontend.os.geteuid", return_value=1000), patch("frontend.subprocess.run") as run:
                payload = {"partuuid": part.identity.partuuid, "filesystem_uuid": part.filesystem_uuid,
                           "occupied": scenario == "occupied"}
                if scenario == "identity":
                    payload["partuuid"] = "other"
                elif scenario == "uuid":
                    payload["filesystem_uuid"] = "0000-1111"
                elif scenario == "type":
                    payload["occupied"] = "false"
                run.return_value = subprocess.CompletedProcess([], int(scenario == "failed"),
                    "invalid" if scenario == "json" else json.dumps(payload), "failed")
                if scenario in ("free", "occupied"):
                    self.assertEqual(probe_esp_occupied(part), scenario == "occupied")
                else:
                    with self.assertRaises(FrontendPlanError):
                        probe_esp_occupied(part)
                self.assertEqual(run.call_args.args[0], ["pkexec", "/usr/bin/anduinos-installer-storage-probe",
                                                        "--esp-inspect", part.identity.path])

    def test_development_mode_never_runs_privileged_probe(self):
        with patch("frontend.subprocess.run") as run:
            self.assertFalse(probe_esp_occupied(None, development_mode=True))
            run.assert_not_called()

    def test_helper_validates_target_isolates_mounts_and_reports_occupied(self):
        disk = external_windows_disk()
        part = disk.partitions[0]
        for occupied in (False, True):
            result = EspReuseInspection(part.identity.partuuid, part.filesystem_uuid, True, 1024**3,
                vendor_entries=(EspTreeEntry("EFI/AnduinOS", "directory", 0),) if occupied else ())
            output = io.StringIO()
            with patch("storage_probe_cli.probe_storage_inventory", return_value=SimpleNamespace(disks=(disk,))), \
                 patch("storage_probe_cli.isolate_mount_namespace") as isolate, \
                 patch("storage_probe_cli.inspect_esp_for_reuse", return_value=result) as inspect, \
                 contextlib.redirect_stdout(output):
                self.assertEqual(main(["--esp-inspect", part.identity.path], geteuid=lambda: 0), 0)
            isolate.assert_called_once_with()
            self.assertEqual(inspect.call_args.args[0], part)
            self.assertEqual(json.loads(output.getvalue())["occupied"], occupied)

    def test_helper_rejects_arbitrary_missing_and_unhealthy_targets(self):
        disk = external_windows_disk()
        for path in ("/tmp/esp", "/dev/not-supported", "/dev/sda99"):
            with patch("storage_probe_cli.probe_storage_inventory", return_value=SimpleNamespace(disks=(disk,))), \
                 patch("storage_probe_cli.inspect_esp_for_reuse") as inspect, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--esp-inspect", path], geteuid=lambda: 0), 2)
                inspect.assert_not_called()
        with patch("storage_probe_cli.probe_storage_inventory", return_value=SimpleNamespace(disks=(disk,))), \
             patch("storage_probe_cli.isolate_mount_namespace"), \
             patch("storage_probe_cli.inspect_esp_for_reuse", side_effect=RuntimeError("unreadable")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--esp-inspect", disk.partitions[0].identity.path], geteuid=lambda: 0), 2)


class EspPageGateTests(unittest.TestCase):
    def code(self, page):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "src/pages.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == page)
        on_next = next(n for n in function.body if isinstance(n, ast.FunctionDef) and n.name == "on_next")
        return compile(ast.Module(body=[on_next], type_ignores=[]), "pages.py", "exec")

    def test_manual_gate_returns_to_new_esp_without_advancing_or_changing_geometry(self):
        for scenario in ("occupied", "free", "error", "stale", "new"):
            with self.subTest(scenario=scenario):
                disk = external_windows_disk()
                root = SimpleNamespace(role=ManualPartitionRole.ROOT, size_mib=32768)
                preview = Mock(spec=ManualStoragePreview)
                preview.selection = SimpleNamespace(new_partitions=(root,), reused_esp_partuuid=(
                    "" if scenario == "new" else disk.partitions[0].identity.partuuid))
                shared = {"manual_storage_preview_model": preview}
                requests = Mock()
                ns = {"ManualStoragePreview": ManualStoragePreview, "ManualPartitionRole": ManualPartitionRole,
                      "shared": shared, "disk": disk, "MIB": 1024**2, "lang": "en_US",
                      "requests": requests, "_": lambda text, lang: text,
                      "_confirm_storage_capacity": lambda page, nav, lang, size, done: done(),
                      "probe_esp_occupied": Mock(return_value=scenario == "occupied")}
                for name in ("page", "nav_view", "workflow", "pulse", "loading", "status", "_set_next",
                             "_replace_draft", "_queue_refresh", "_esp_conflict_dialog", "build_user_page"):
                    ns[name] = Mock()
                exec(self.code("build_advanced_storage_page"), ns)
                ns["on_next"]()
                if scenario == "new":
                    requests.start.assert_not_called()
                else:
                    worker, callback = requests.start.call_args.args
                    if scenario == "stale":
                        shared["manual_storage_preview_model"] = None
                    callback(worker(), RuntimeError("read failed") if scenario == "error" else None)
                self.assertEqual(ns["nav_view"].push.called, scenario in ("free", "new"))
                if scenario == "occupied":
                    ns["_replace_draft"].assert_called_once_with(reused_esp_partuuid="")
                    ns["_esp_conflict_dialog"].assert_called_once()
                else:
                    ns["_replace_draft"].assert_not_called()

    def test_guided_gate_selects_new_esp_only_when_extent_has_room(self):
        for room in (False, True):
            with self.subTest(room=room):
                esp = external_windows_disk().partitions[0]
                selected = SimpleNamespace(free_extent_id="free", reused_esp_partuuid=esp.identity.partuuid)
                ns = {"shared": {}, "workflow": Mock(), "lang": "en_US",
                      "_": lambda text, lang: text, "esp_options": [esp, None] if room else [esp],
                      "_guided_selection": lambda: selected,
                      "build_guided_storage_preview": Mock(return_value=SimpleNamespace(reused_esp=esp)),
                      "probe_esp_occupied": Mock(return_value=True)}
                for name in ("requests", "_finish_loading", "_set_storage_controls", "_set_next", "guidance",
                             "esp_dropdown", "_esp_conflict_dialog", "nav_view", "build_user_page",
                             "loading_label", "loading", "pulse"):
                    ns[name] = Mock()
                exec(self.code("build_guided_storage_page"), ns)
                ns["on_next"]()
                worker, callback = ns["requests"].start.call_args.args
                callback(worker(), None)
                self.assertEqual(ns["esp_dropdown"].set_selected.called, room)
                ns["_esp_conflict_dialog"].assert_called_once()
                ns["nav_view"].push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
