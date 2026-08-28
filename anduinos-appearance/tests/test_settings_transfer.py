import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SRC = pathlib.Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))

from anduinos_appearance import settings_transfer  # noqa: E402


class SettingsTransferTests(unittest.TestCase):
    DUMP = b"[/]\ncolor-scheme='prefer-dark'\n"
    IMPORT = b"[desktop/interface]\nclock-show-seconds=true\n"

    @staticmethod
    def completed(command, stdout=b""):
        return subprocess.CompletedProcess(command, 0, stdout, b"")

    def fake_successful_dconf(self, command, **kwargs):
        stdout = self.DUMP if command[:2] == ["dconf", "dump"] else b""
        return self.completed(command, stdout)

    def test_dump_is_returned_unmodified(self):
        with mock.patch.object(
            settings_transfer.subprocess,
            "run",
            side_effect=self.fake_successful_dconf,
        ) as run:
            result = settings_transfer.dump_gnome_settings()

        self.assertEqual(result, self.DUMP)
        run.assert_called_once_with(
            ["dconf", "dump", "/org/gnome/"],
            check=True,
            capture_output=True,
        )

    def test_merge_import_does_not_reset_existing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = pathlib.Path(directory) / "backup.ini"
            with mock.patch.object(
                settings_transfer.subprocess,
                "run",
                side_effect=self.fake_successful_dconf,
            ) as run:
                result = settings_transfer.import_gnome_settings(
                    self.IMPORT,
                    strict=False,
                    backup_path=backup,
                )

            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(
                commands,
                [
                    ["dconf", "dump", "/org/gnome/"],
                    ["dconf", "load", "/org/gnome/"],
                ],
            )
            self.assertEqual(backup.read_bytes(), self.DUMP)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result, backup)

    def test_strict_import_resets_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = pathlib.Path(directory) / "backup.ini"
            with mock.patch.object(
                settings_transfer.subprocess,
                "run",
                side_effect=self.fake_successful_dconf,
            ) as run:
                settings_transfer.import_gnome_settings(
                    self.IMPORT,
                    strict=True,
                    backup_path=backup,
                )

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["dconf", "dump", "/org/gnome/"],
                ["dconf", "reset", "-f", "/org/gnome/"],
                ["dconf", "load", "/org/gnome/"],
            ],
        )

    def test_failed_strict_import_restores_previous_snapshot(self):
        calls = []

        def fail_first_load(command, **kwargs):
            calls.append((command, kwargs.get("input")))
            if command[:2] == ["dconf", "dump"]:
                return self.completed(command, self.DUMP)
            if command[:2] == ["dconf", "load"] and kwargs.get("input") == self.IMPORT:
                raise subprocess.CalledProcessError(1, command, stderr=b"bad input")
            return self.completed(command)

        with tempfile.TemporaryDirectory() as directory:
            backup = pathlib.Path(directory) / "backup.ini"
            with mock.patch.object(
                settings_transfer.subprocess,
                "run",
                side_effect=fail_first_load,
            ):
                with self.assertRaises(settings_transfer.SettingsImportError) as raised:
                    settings_transfer.import_gnome_settings(
                        self.IMPORT,
                        strict=True,
                        backup_path=backup,
                    )

        self.assertTrue(raised.exception.rollback_succeeded)
        self.assertEqual(
            [command for command, unused_input in calls],
            [
                ["dconf", "dump", "/org/gnome/"],
                ["dconf", "reset", "-f", "/org/gnome/"],
                ["dconf", "load", "/org/gnome/"],
                ["dconf", "reset", "-f", "/org/gnome/"],
                ["dconf", "load", "/org/gnome/"],
            ],
        )
        self.assertEqual(calls[-1][1], self.DUMP)

    def test_empty_or_non_keyfile_input_is_rejected_before_dconf_runs(self):
        for contents in (b"", b"not a dconf dump\n"):
            with self.subTest(contents=contents):
                with mock.patch.object(settings_transfer.subprocess, "run") as run:
                    with self.assertRaises(settings_transfer.InvalidSettingsFile):
                        settings_transfer.import_gnome_settings(
                            contents,
                            strict=False,
                        )
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
