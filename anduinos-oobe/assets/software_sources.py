"""Optional, explicitly requested software-source changes for the running system."""

from pathlib import Path
import os
import pwd
import subprocess
import tempfile
import urllib.request


CHINA_URL = 'https://packages.aiursoft.cn/artifacts/anduinos/'
FLATHUB_USTC_URL = 'https://mirrors.ustc.edu.cn/flathub'
PACKAGE_DIR = Path('/usr/share/anduinos-oobe/china-config')


def _run(command, timeout=30, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True,
                            timeout=timeout, **kwargs)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()
                           or f'{command[0]} failed')
    return result


def china_apt_configured():
    try:
        status = _run(['dpkg-query', '--show', '--showformat=${db:Status-Abbrev}',
                       'anduinos-apt-config-for-china']).stdout
        return status.startswith('ii') and CHINA_URL in Path(
            '/etc/apt/sources.list.d/anduinos.sources').read_text()
    except Exception:
        return False


def probe_apt(package, work):
    """Verify signed repository metadata without modifying active APT state."""
    _run(['dpkg-deb', '--extract', str(package), str(work / 'payload')])
    for folder in ('lists/partial', 'cache/archives/partial'):
        (work / folder).mkdir(parents=True)
    config = work / 'apt.conf'
    config.write_text(
        'Dir::Etc::main "-";\nDir::Etc::parts "-";\n'
        'Dir::Etc::sourceparts "-";\n'
        f'Dir::Etc::sourcelist "{work}/payload/etc/apt/sources.list.d/anduinos.sources";\n'
        f'Dir::State::lists "{work}/lists";\n'
        f'Dir::Cache "{work}/cache";\n'
        f'APT::Sandbox::User "{pwd.getpwuid(os.getuid()).pw_name}";\n'
        'Acquire::Retries "0";\nAcquire::http::Timeout "10";\n'
        'Acquire::https::Timeout "10";\nAPT::Update::Error-Mode "any";\n')
    _run(['apt-get', 'update'], timeout=60,
         env={**os.environ, 'APT_CONFIG': str(config)})


def probe_flathub():
    for name in ('config', 'summary.idx'):
        request = urllib.request.Request(
            f'{FLATHUB_USTC_URL}/{name}',
            headers={'Range': 'bytes=0-255', 'User-Agent': 'anduinos-oobe'})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read(256)
            if not data or 'text/html' in response.headers.get('Content-Type', '').lower():
                raise RuntimeError(f'Invalid USTC metadata: {name}')
            if name == 'config' and b'[core]' not in data:
                raise RuntimeError('Invalid USTC OSTree config')


def switch_to_china(package_dir=PACKAGE_DIR):
    """Probe both sources before changes; report each failure independently."""
    warnings = []
    package = None
    apt_ready = flatpak_ready = False
    with tempfile.TemporaryDirectory(prefix='anduinos-oobe-sources-') as directory:
        try:
            arch = _run(['dpkg', '--print-architecture']).stdout.strip()
            if arch not in ('amd64', 'arm64'):
                raise RuntimeError('Unsupported package architecture')
            package = Path(package_dir) / f'{arch}.deb'
            probe_apt(package, Path(directory))
            apt_ready = True
        except Exception as error:
            warnings.append(f'Kept the existing AnduinOS source: {error}')
        try:
            probe_flathub()
            flatpak_ready = True
        except Exception as error:
            warnings.append(f'Kept the existing Flathub source: {error}')

        if apt_ready:
            try:
                if not china_apt_configured():
                    _run(['pkexec', '/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive',
                          'apt-get', '--yes', '--no-install-recommends',
                          '-o', 'Acquire::Retries=0', '-o', 'Acquire::https::Timeout=10',
                          'install', str(package)], timeout=120)
                    if not china_apt_configured():
                        raise RuntimeError('China APT configuration was not installed')
            except Exception as error:
                warnings.append(f'Could not complete the China APT configuration: {error}')
        if flatpak_ready:
            try:
                remotes = _run(['flatpak', 'remotes', '--system', '--show-disabled',
                                '--columns=name']).stdout.splitlines()
                if 'flathub' not in [name.strip() for name in remotes]:
                    _run(['pkexec', 'flatpak', '--system', 'remote-add', '--if-not-exists',
                          'flathub', 'https://dl.flathub.org/repo/flathub.flatpakrepo'], timeout=120)
                _run(['pkexec', 'flatpak', '--system', 'remote-modify', '--enable',
                      '--url=' + FLATHUB_USTC_URL, 'flathub'], timeout=60)
            except Exception as error:
                warnings.append(f'Could not change the system Flathub source: {error}')
    return warnings
