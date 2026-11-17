"""Optional, target-only regional source changes after explicit plan consent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
import urllib.request

from .command import CommandRunner
from .software import _target
from .steps import FailurePolicy, InstallContext, StepWarning


CHINA_CONFIG_DEB = Path('/usr/share/anduinos-installer-beta/china-apt-config.deb')
FLATHUB_USTC_URL = 'https://mirrors.ustc.edu.cn/flathub'


def probe_flathub() -> None:
    """Check OSTree metadata, not an HTML mirror landing page."""
    for name in ('config', 'summary.idx'):
        request = urllib.request.Request(
            f'{FLATHUB_USTC_URL}/{name}',
            headers={'Range': 'bytes=0-255', 'User-Agent': 'anduinos-installer'},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read(256)
            content_type = response.headers.get('Content-Type', '')
            if not data or 'text/html' in content_type.lower():
                raise RuntimeError(f'USTC returned invalid OSTree metadata: {name}')
            if name == 'config' and b'[core]' not in data:
                raise RuntimeError('USTC did not return an OSTree config')


@dataclass
class SwitchChinaSourcesStep:
    runner: CommandRunner
    id: str = 'switch-china-sources'
    title: str = 'Switch to mainland China software sources'
    failure_policy: FailurePolicy = FailurePolicy.WARNING
    progress_weight: int = 2
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        # Optional tools and connectivity are checked during execution. A
        # missing mirror must never prevent the disk installation from starting.
        context.validate_plan()

    def execute(self, context: InstallContext) -> None:
        if not context.plan.software.download_from_china:
            return
        if context.values.get('network_online') is False:
            raise StepWarning('Chinese mirrors were skipped because the installer is offline')
        target = _target(context)
        warnings = []
        temporary_root = target / 'var/tmp'
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='anduinos-china-', dir=temporary_root) as directory:
            work = Path(directory)
            inside = '/' + str(work.relative_to(target))
            # Both probes finish before either active repository is changed.
            apt_ready = False
            try:
                self._probe_apt(target, work, inside)
                apt_ready = True
            except Exception as error:
                context.log(f'China APT probe failed: {error}')
                warnings.append('Kept the existing AnduinOS package source')
            flatpak_ready = False
            try:
                probe_flathub()
                flatpak_ready = True
            except Exception as error:
                context.log(f'USTC Flathub probe failed: {error}')
                warnings.append('Kept the existing Flathub source')

            if apt_ready:
                try:
                    self.runner.run((
                        'chroot', str(target), '/usr/bin/env',
                        'DEBIAN_FRONTEND=noninteractive', 'apt-get', '--yes',
                        '--no-install-recommends', '-o', 'Acquire::Retries=0',
                        '-o', 'Acquire::https::Timeout=10', 'install',
                        f'{inside}/china-apt-config.deb',
                    ), timeout=120)
                    self.runner.run((
                        'chroot', str(target), 'dpkg-query', '--show',
                        '--showformat=${db:Status-Abbrev}', 'anduinos-apt-config-for-china',
                    ), timeout=15)
                    sources = target / 'etc/apt/sources.list.d/anduinos.sources'
                    if 'https://packages.aiursoft.cn/artifacts/anduinos/' not in sources.read_text():
                        raise RuntimeError('China APT source was not configured')
                    context.values['package_indexes_refreshed'] = False
                    context.log('Installed anduinos-apt-config-for-china in the target')
                except Exception as error:
                    context.log(f'China APT switch could not be completed: {error}')
                    warnings.append('Could not complete the China APT configuration')
            if flatpak_ready:
                try:
                    self.runner.run((
                        'chroot', str(target), 'flatpak', '--system', 'remote-modify',
                        '--url=' + FLATHUB_USTC_URL, 'flathub',
                    ), timeout=60)
                    context.log('Target system Flathub now uses USTC')
                except Exception as error:
                    context.log(f'USTC Flathub switch could not be completed: {error}')
                    warnings.append('Could not change the system Flathub source')
        if warnings:
            raise StepWarning('; '.join(warnings) + '; installation will continue')

    def _probe_apt(self, target: Path, work: Path, inside: str) -> None:
        """APT verifies the proposed sources using the installed archive keyring.

        The bundled package is built by the ordinary China .aosproj. The probe
        has private source/index/cache/config paths and never rewrites the
        active sources or calls dpkg installation scripts.
        """
        shutil.copyfile(CHINA_CONFIG_DEB, work / 'china-apt-config.deb')
        self.runner.run((
            'dpkg-deb', '--extract', str(work / 'china-apt-config.deb'),
            str(work / 'payload'),
        ), timeout=15)
        for folder in ('lists/partial', 'cache/archives/partial'):
            (work / folder).mkdir(parents=True)
        config = work / 'apt.conf'
        config.write_text(
            'Dir::Etc::main "-";\nDir::Etc::parts "-";\n'
            'Dir::Etc::sourceparts "-";\n'
            f'Dir::Etc::sourcelist "{inside}/payload/etc/apt/sources.list.d/anduinos.sources";\n'
            f'Dir::State::lists "{inside}/lists";\n'
            f'Dir::Cache "{inside}/cache";\n'
            'APT::Sandbox::User "root";\nAcquire::Retries "0";\n'
            'Acquire::http::Timeout "10";\nAcquire::https::Timeout "10";\n'
            'APT::Update::Error-Mode "any";\n',
            encoding='utf-8',
        )
        self.runner.run((
            'chroot', str(target), '/usr/bin/env', f'APT_CONFIG={inside}/apt.conf',
            'apt-get', 'update',
        ), timeout=60)

    def verify(self, context: InstallContext) -> None:
        return None

    def cleanup(self, context: InstallContext) -> None:
        return None
