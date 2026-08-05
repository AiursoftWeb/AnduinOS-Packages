import os
import shutil
import subprocess
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


def filename(uuid):
    return f"90-NM-{uuid}.yaml"


def profile(uuid, *, ssid="Home", secret="correct-horse"):
    return (
        "network:\n"
        "  version: 2\n"
        "  wifis:\n"
        f"    NM-{uuid}:\n"
        "      renderer: NetworkManager\n"
        "      match:\n"
        "        name: wlan0\n"
        "      dhcp4: true\n"
        "      access-points:\n"
        f"        {ssid!r}:\n"
        "          auth:\n"
        "            key-management: psk\n"
        f"            password: {secret!r}\n"
        "          networkmanager:\n"
        f"            uuid: {uuid}\n"
        f"            name: {ssid!r}\n"
        "      networkmanager:\n"
        f"        uuid: {uuid}\n"
        f"        name: {ssid!r}\n"
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

    def write_profile(self, directory, uuid, payload=None):
        path = directory / filename(uuid)
        path.write_bytes(payload if payload is not None else profile(uuid))
        path.chmod(0o600)
        return path

    def context(self, target, logs=None):
        return InstallContext(
            valid_plan(),
            (logs if logs is not None else []).append,
            values={"target": target},
        )

    def test_only_active_wifi_netplan_is_migrated_with_safe_permissions(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n"
            f"{OTHER_UUID}:vpn\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-netplan"
            source.mkdir(mode=0o755)
            active_payload = profile(ACTIVE_UUID)
            self.write_profile(source, ACTIVE_UUID, active_payload)
            self.write_profile(
                source, OTHER_UUID, profile(OTHER_UUID, ssid="Old")
            )
            target = root / "target"
            target.mkdir()
            context = self.context(target)
            step = self.make_step(runner, source)

            step.preflight(context)
            step.execute(context)
            step.verify(context)

            destination = target / "etc/netplan" / filename(ACTIVE_UUID)
            self.assertEqual(destination.read_bytes(), active_payload)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertFalse(
                (target / "etc/netplan" / filename(OTHER_UUID)).exists()
            )
            validation = (
                "netplan",
                "generate",
                "--root-dir",
                str(target),
                "--mapping",
                f"NM-{ACTIVE_UUID}",
            )
            self.assertIn(
                (
                    validation,
                    {"check": False, "timeout": 30, "log_output": False},
                ),
                runner.commands,
            )

            step.cleanup(context)
            self.assertFalse(destination.exists())

    @unittest.skipUnless(shutil.which("netplan"), "netplan is not installed")
    def test_netplan_fixture_has_the_expected_networkmanager_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            netplan = target / "etc/netplan"
            netplan.mkdir(parents=True)
            base = netplan / "01-network-manager-all.yaml"
            base.write_text(
                "network:\n  version: 2\n  renderer: NetworkManager\n"
            )
            base.chmod(0o600)
            self.write_profile(netplan, ACTIVE_UUID)

            result = subprocess.run(
                (
                    "netplan",
                    "generate",
                    "--root-dir",
                    str(target),
                    "--mapping",
                    f"NM-{ACTIVE_UUID}",
                ),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"id=NM-{ACTIVE_UUID}", result.stdout)

    def test_no_active_wifi_is_a_successful_noop(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{OTHER_UUID}:ethernet\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-netplan"
            source.mkdir(mode=0o755)
            target = root / "target"
            target.mkdir()
            context = self.context(target)
            step = self.make_step(runner, source)

            step.preflight(context)
            step.execute(context)
            step.verify(context)

            self.assertFalse((target / "etc/netplan").exists())

    def test_network_manager_probe_failure_is_a_visible_warning(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = ("", "not running", 10)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            context = self.context(target)
            step = self.make_step(runner, root / "missing")

            result = StepRunner([step]).run(context)

            self.assertTrue(result.succeeded)
            self.assertEqual(result.results[0].status, StepStatus.WARNING)
            self.assertIn("NetworkManager", result.results[0].message)

    def test_symlink_and_group_readable_netplans_are_rejected(self):
        for unsafe_kind in ("symlink", "group-readable"):
            with self.subTest(unsafe_kind=unsafe_kind):
                runner = FakeRunner()
                runner.outputs[ACTIVE_WIFI_COMMAND] = (
                    f"{ACTIVE_UUID}:802-11-wireless\n",
                    "",
                    0,
                )
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / "live-netplan"
                    source.mkdir(mode=0o755)
                    if unsafe_kind == "symlink":
                        real = source / "real.yaml"
                        real.write_bytes(profile(ACTIVE_UUID))
                        real.chmod(0o600)
                        (source / filename(ACTIVE_UUID)).symlink_to(real)
                    else:
                        unsafe = self.write_profile(source, ACTIVE_UUID)
                        unsafe.chmod(0o640)
                    target = root / "target"
                    target.mkdir()
                    context = self.context(target)
                    step = self.make_step(runner, source)

                    step.preflight(context)
                    with self.assertRaises(StepWarning):
                        step.execute(context)

                    self.assertEqual(
                        context.values["wifi_profile_snapshots"], ()
                    )
                    self.assertFalse((target / "etc/netplan").exists())

    def test_nonstandard_netplan_filename_is_not_migrated(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-netplan"
            source.mkdir(mode=0o755)
            wrong = source / "99-wifi.yaml"
            wrong.write_bytes(profile(ACTIVE_UUID))
            wrong.chmod(0o600)
            target = root / "target"
            target.mkdir()
            context = self.context(target)
            step = self.make_step(runner, source)

            step.preflight(context)
            with self.assertRaises(StepWarning):
                step.execute(context)

            self.assertEqual(context.values["wifi_profile_snapshots"], ())
            self.assertFalse((target / "etc/netplan").exists())

    def test_existing_target_netplan_is_never_overwritten(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-netplan"
            source.mkdir(mode=0o755)
            self.write_profile(source, ACTIVE_UUID)
            target = root / "target"
            target_directory = target / "etc/netplan"
            target_directory.mkdir(parents=True)
            existing_payload = profile(ACTIVE_UUID, secret="keep-me")
            existing = self.write_profile(
                target_directory, ACTIVE_UUID, existing_payload
            )
            context = self.context(target)
            step = self.make_step(runner, source)

            step.preflight(context)
            step.execute(context)
            step.verify(context)

            self.assertEqual(existing.read_bytes(), existing_payload)
            step.cleanup(context)
            self.assertEqual(existing.read_bytes(), existing_payload)

    def test_profile_change_after_preflight_is_rejected(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-netplan"
            source.mkdir(mode=0o755)
            live_profile = self.write_profile(source, ACTIVE_UUID)
            target = root / "target"
            target.mkdir()
            context = self.context(target)
            step = self.make_step(runner, source)
            step.preflight(context)
            live_profile.write_bytes(profile(ACTIVE_UUID, secret="changed"))
            live_profile.chmod(0o600)

            with self.assertRaisesRegex(RuntimeError, "changed after preflight"):
                step.execute(context)

            self.assertFalse(
                (target / "etc/netplan" / filename(ACTIVE_UUID)).exists()
            )

    def test_failed_netplan_validation_removes_only_created_profile(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-netplan"
            source.mkdir(mode=0o755)
            self.write_profile(source, ACTIVE_UUID)
            target = root / "target"
            target.mkdir()
            validation = (
                "netplan",
                "generate",
                "--root-dir",
                str(target),
                "--mapping",
                f"NM-{ACTIVE_UUID}",
            )
            runner.outputs[validation] = ("", "invalid YAML", 1)
            context = self.context(target)
            step = self.make_step(runner, source)

            result = StepRunner([step]).run(context)

            self.assertTrue(result.succeeded)
            self.assertEqual(result.results[0].status, StepStatus.WARNING)
            self.assertIn("Netplan", result.results[0].message)
            self.assertFalse(
                (target / "etc/netplan" / filename(ACTIVE_UUID)).exists()
            )

    def test_wifi_secret_is_never_logged(self):
        runner = FakeRunner()
        runner.outputs[ACTIVE_WIFI_COMMAND] = (
            f"{ACTIVE_UUID}:802-11-wireless\n",
            "",
            0,
        )
        secret = "never-log-this-password"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live-netplan"
            source.mkdir(mode=0o755)
            self.write_profile(
                source, ACTIVE_UUID, profile(ACTIVE_UUID, secret=secret)
            )
            target = root / "target"
            target.mkdir()
            logs = []
            context = self.context(target, logs)
            step = self.make_step(runner, source)

            result = StepRunner([step]).run(context)

            self.assertTrue(result.succeeded)
            self.assertNotIn(secret, "\n".join(logs))


if __name__ == "__main__":
    unittest.main()
