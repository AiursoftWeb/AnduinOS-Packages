import os
import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.steps import (
    InstallContext,
    StepRunner,
    StepStatus,
    StepWarning,
)
from installer_core.wifi_migration import (
    ACTIVE_WIFI_COMMAND,
    MigrateWifiConnectionStep,
)


ACTIVE_UUID = "12345678-1234-5678-9abc-123456789abc"
OTHER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def profile(uuid, *, ssid="Home", secret="correct-horse"):
    return (
        "[connection]\n"
        f"id={ssid}\n"
        f"uuid={uuid}\n"
        "type=wifi\n\n"
        "[wifi]\n"
        f"ssid={ssid}\n\n"
        "[wifi-security]\n"
        f"psk={secret}\n"
    ).encode()


class WifiMigrationTests(unittest.TestCase):
    def make_step(self, runner, source):
        return MigrateWifiConnectionStep(
            runner,
            source_directory=source,
            source_uid=os.getuid(),
            target_uid=os.getuid(),
            target_gid=os.getgid(),
        )

    def write_profile(self, directory, name, payload):
        path = directory / name
        path.write_bytes(payload)
        path.chmod(0o600)
        return path

    def test_only_active_wifi_profile_is_migrated_with_safe_permissions(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n"
            f"{OTHER_UUID}:vpn\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-connections"
            source.mkdir()
            active_payload = profile(ACTIVE_UUID)
            self.write_profile(source, "Home.nmconnection", active_payload)
            self.write_profile(
                source, "Old.nmconnection", profile(OTHER_UUID, ssid="Old")
            )
            target = root / "target"
            target.mkdir()
            context = InstallContext(
                valid_plan(), lambda _message: None, values={"target": target}
            )
            step = self.make_step(runner, source)

            step.preflight(context)
            step.execute(context)
            step.verify(context)

            destination = (
                target
                / "etc/NetworkManager/system-connections/Home.nmconnection"
            )
            self.assertEqual(destination.read_bytes(), active_payload)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertFalse(
                (
                    target
                    / "etc/NetworkManager/system-connections/Old.nmconnection"
                ).exists()
            )

            step.cleanup(context)
            self.assertFalse(destination.exists())

    def test_no_active_wifi_is_a_successful_noop(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{OTHER_UUID}:ethernet\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-connections"
            source.mkdir()
            target = root / "target"
            target.mkdir()
            context = InstallContext(
                valid_plan(), lambda _message: None, values={"target": target}
            )
            step = self.make_step(runner, source)

            step.preflight(context)
            step.execute(context)
            step.verify(context)

            self.assertFalse((target / "etc/NetworkManager").exists())

    def test_network_manager_probe_failure_is_a_visible_warning(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = ("", "not running", 10)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            context = InstallContext(
                valid_plan(), lambda _message: None, values={"target": target}
            )
            step = self.make_step(runner, root / "missing")

            result = StepRunner([step]).run(context)

            self.assertTrue(result.succeeded)
            self.assertEqual(result.results[0].status, StepStatus.WARNING)
            self.assertIn("NetworkManager", result.results[0].message)

    def test_symlink_and_group_readable_profiles_are_rejected(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-connections"
            source.mkdir()
            real = self.write_profile(
                source, "unsafe.nmconnection", profile(ACTIVE_UUID)
            )
            real.chmod(0o640)
            (source / "link.nmconnection").symlink_to(real)
            target = root / "target"
            target.mkdir()
            context = InstallContext(
                valid_plan(), lambda _message: None, values={"target": target}
            )
            step = self.make_step(runner, source)

            step.preflight(context)
            with self.assertRaises(StepWarning):
                step.execute(context)

            self.assertEqual(context.values["wifi_profile_snapshots"], ())
            self.assertFalse((target / "etc/NetworkManager").exists())

    def test_non_wifi_profile_with_active_uuid_is_rejected(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-connections"
            source.mkdir()
            payload = profile(ACTIVE_UUID).replace(b"type=wifi", b"type=vpn")
            self.write_profile(source, "Wrong.nmconnection", payload)
            target = root / "target"
            target.mkdir()
            context = InstallContext(
                valid_plan(), lambda _message: None, values={"target": target}
            )
            step = self.make_step(runner, source)

            step.preflight(context)
            with self.assertRaises(StepWarning):
                step.execute(context)

            self.assertEqual(context.values["wifi_profile_snapshots"], ())
            self.assertFalse((target / "etc/NetworkManager").exists())

    def test_existing_target_uuid_is_never_overwritten(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-connections"
            source.mkdir()
            self.write_profile(source, "Home.nmconnection", profile(ACTIVE_UUID))
            target = root / "target"
            target_directory = (
                target / "etc/NetworkManager/system-connections"
            )
            target_directory.mkdir(parents=True)
            existing_payload = profile(ACTIVE_UUID, secret="keep-me")
            existing = self.write_profile(
                target_directory, "Existing.nmconnection", existing_payload
            )
            context = InstallContext(
                valid_plan(), lambda _message: None, values={"target": target}
            )
            step = self.make_step(runner, source)

            step.preflight(context)
            step.execute(context)

            self.assertEqual(existing.read_bytes(), existing_payload)
            self.assertFalse((target_directory / "Home.nmconnection").exists())

    def test_profile_change_after_preflight_is_rejected(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-connections"
            source.mkdir()
            live_profile = self.write_profile(
                source, "Home.nmconnection", profile(ACTIVE_UUID)
            )
            target = root / "target"
            target.mkdir()
            context = InstallContext(
                valid_plan(), lambda _message: None, values={"target": target}
            )
            step = self.make_step(runner, source)
            step.preflight(context)
            live_profile.write_bytes(profile(ACTIVE_UUID, secret="changed"))
            live_profile.chmod(0o600)

            with self.assertRaisesRegex(RuntimeError, "changed after preflight"):
                step.execute(context)

            self.assertFalse(
                (
                    target
                    / "etc/NetworkManager/system-connections/Home.nmconnection"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
