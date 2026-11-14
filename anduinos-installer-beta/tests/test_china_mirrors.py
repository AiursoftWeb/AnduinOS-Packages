"""Explicit regional consent and best-effort target source selection."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.china_mirrors import SwitchChinaSourcesStep
from installer_core.executor import InstallerExecutor
from installer_core.model import InstallPlan
from installer_core.steps import InstallContext, StepRunner, StepStatus, StepWarning
from installer_core.validation import validate_plan, PlanValidationError


class ChinaMirrorTests(unittest.TestCase):
    def plan(self):
        plan = valid_plan()
        return replace(plan, software=replace(plan.software, download_from_china=True))

    def test_false_default_and_legacy_plan_keep_original_pipeline(self):
        plan = valid_plan()
        self.assertFalse(plan.software.download_from_china)
        value = plan.to_dict()
        del value['software']['download_from_china']
        self.assertFalse(InstallPlan.from_dict(value).software.download_from_china)
        steps = InstallerExecutor(lambda _: None).build_steps(plan)
        self.assertNotIn('switch-china-sources', [step.id for step in steps])
        with patch('installer_core.china_mirrors.probe_flathub') as probe:
            runner = FakeRunner()
            SwitchChinaSourcesStep(runner).execute(InstallContext(plan, lambda _: None))
        probe.assert_not_called()
        self.assertEqual(runner.commands, [])

    def test_true_round_trips_and_is_validated_as_boolean(self):
        plan = self.plan()
        self.assertTrue(InstallPlan.from_dict(plan.to_dict()).software.download_from_china)
        invalid = replace(plan, software=replace(plan.software, download_from_china='true'))
        with self.assertRaises(PlanValidationError):
            validate_plan(invalid)

    def test_optional_step_precedes_input_methods_and_follows_dkms_identity(self):
        ids = [step.id for step in InstallerExecutor(lambda _: None).build_steps(self.plan())]
        self.assertLess(ids.index('prepare-secure-boot'), ids.index('switch-china-sources'))
        self.assertLess(ids.index('switch-china-sources'), ids.index('install-language-packs'))
        self.assertLess(ids.index('switch-china-sources'), ids.index('install-input-method'))

    def context(self, target, online=True):
        return InstallContext(self.plan(), lambda _: None, {
            'target': target, 'chroot_environment_ready': True, 'network_online': online,
            'package_indexes_refreshed': True,
        })

    def test_offline_warns_without_any_probe_or_source_change(self):
        runner = FakeRunner()
        with patch('installer_core.china_mirrors.probe_flathub') as probe:
            with self.assertRaisesRegex(StepWarning, 'offline'):
                SwitchChinaSourcesStep(runner).execute(self.context(Path('/unused'), False))
        probe.assert_not_called()
        self.assertEqual(runner.commands, [])

    def test_both_probes_precede_changes_and_one_failure_keeps_its_source(self):
        for apt_ok, flatpak_ok in [(True, True), (False, True), (True, False), (False, False)]:
            with self.subTest(apt=apt_ok, flatpak=flatpak_ok), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                sources = target / 'etc/apt/sources.list.d/anduinos.sources'
                sources.parent.mkdir(parents=True)
                sources.write_text('original')
                events = []
                class Runner(FakeRunner):
                    def run(self, command, **kwargs):
                        events.append('change')
                        if 'install' in command:
                            sources.write_text('https://packages.aiursoft.cn/artifacts/anduinos/')
                        return super().run(command, **kwargs)
                runner = Runner()
                step = SwitchChinaSourcesStep(runner)
                context = self.context(target)
                def apt_probe(*args):
                    events.append('apt-probe')
                    if not apt_ok:
                        raise RuntimeError('unreachable')
                def flatpak_probe():
                    events.append('flatpak-probe')
                    if not flatpak_ok:
                        raise RuntimeError('unreachable')
                with patch.object(step, '_probe_apt', side_effect=apt_probe), patch(
                    'installer_core.china_mirrors.probe_flathub', side_effect=flatpak_probe,
                ):
                    if apt_ok and flatpak_ok:
                        step.execute(context)
                    else:
                        with self.assertRaises(StepWarning):
                            step.execute(context)
                self.assertEqual(events[:2], ['apt-probe', 'flatpak-probe'])
                commands = [c for c, _ in runner.commands]
                self.assertEqual(any('install' in c for c in commands), apt_ok)
                self.assertEqual(any('remote-modify' in c for c in commands), flatpak_ok)
                self.assertTrue(all(c[:2] == ('chroot', str(target)) for c in commands))
                if apt_ok:
                    self.assertFalse(context.values['package_indexes_refreshed'])
                else:
                    self.assertEqual(sources.read_text(), 'original')
                self.assertEqual(list((target / 'var/tmp').iterdir()), [])

    def test_switch_failure_is_warning_and_does_not_abort_installation(self):
        class FailedSwitch(FakeRunner):
            def run(self, command, **kwargs):
                raise RuntimeError('optional switch failed')
        with tempfile.TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            step = SwitchChinaSourcesStep(FailedSwitch())
            with patch.object(step, '_probe_apt'), patch('installer_core.china_mirrors.probe_flathub'):
                result = StepRunner([step]).run(context)
            self.assertTrue(result.succeeded)
            self.assertEqual(result.results[0].status, StepStatus.WARNING)
