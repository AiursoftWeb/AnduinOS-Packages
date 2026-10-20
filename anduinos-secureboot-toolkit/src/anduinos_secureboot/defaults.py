"""Converge future signing defaults after enrollment; never re-sign old modules.

Called under the toolkit lock at boot, package configuration and enrollment
completion. DKMS points at an immutable pair before the compatibility copies
are replaced. A durable, root-only backup precedes every changed transaction.
Interrupted transactions can be retried without losing the original backup.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
import tempfile

from . import identity

CONFIG = Path('/etc/dkms/framework.conf.d/anduinos-sb-sign.conf')
# OOBE c63bc130 wrote this before installing Xbox drivers. 4ae49bc5 stopped
# writing it, but existing installations retained the runtime-generated file.
OOBE_CONFIG = (
    'sign_tool="/etc/dkms/sign_helper.sh"\n'
    'mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"\n'
    'mok_certificate="/var/lib/shim-signed/mok/MOK.der"\n'
)


def configuration(key: Path, cert: Path) -> str:
    return f'mok_signing_key="{key}"\nmok_certificate="{cert}"\n'


def configuration_conflict(config: Path, ignored: Path | None = None) -> Path | None:
    for other in [config.parent.parent / 'framework.conf', *sorted(config.parent.glob('*.conf'))]:
        if other not in {config, ignored} and other.exists() and re.search(
                r'^\s*(?:export\s+)?(?:mok_signing_key|mok_certificate|sign_file|sign_tool)\s*=',
                other.read_text(), re.MULTILINE):
            return other
    return None


def obsolete_oobe_config(config: Path) -> Path | None:
    path = config.parent / 'mok.conf'
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.read_bytes() != OOBE_CONFIG.encode():
        return None
    # A real helper may be a user's customization. Never remove its hook.
    helper = config.parent.parent / 'sign_helper.sh'
    if helper.exists() or helper.is_symlink():
        return None
    safe_path(path.parent)
    # The original OOBE created root:root 0664 files; permit that specific
    # group-writable case, not files writable by other users/groups.
    if (info.st_uid != os.geteuid() or info.st_gid not in {0, os.getegid()}
            or info.st_mode & 0o002):
        raise ValueError(f'Unsafe legacy signing configuration: {path}')
    return path


def defaults_ready(key: Path, cert: Path, legacy: Path | None = None,
                   config: Path = CONFIG) -> bool:
    legacy = legacy or identity.LEGACY_KEY.parent
    try:
        return (configuration_conflict(config) is None
                and config.read_text() in {configuration(key, cert),
                                          configuration(legacy / 'MOK.priv', legacy / 'MOK.der')}
                and not (legacy / 'MOK.priv').is_symlink()
                and not (legacy / 'MOK.der').is_symlink()
                and (legacy / 'MOK.der').read_bytes() == cert.read_bytes()
                and (legacy / 'MOK.priv').is_file()
                and not (legacy / 'MOK.priv').stat().st_mode & 0o077)
    except OSError:
        return False


def safe_path(path: Path) -> None:
    # Refuse redirected or user-writable ancestors before privileged writes.
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f'Redirected signing path: {item}')
        if item.exists() and (item.stat().st_uid not in {0, os.geteuid()}
                              or item.stat().st_mode & 0o022):
            # /tmp is allowed only as a parent of private test directories.
            if item == Path('/tmp') and item.stat().st_mode & 0o1000:
                continue
            raise ValueError(f'Unsafe signing path ownership or permissions: {item}')


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.signing-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
            descriptor = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)


def sync_defaults(run, root: Path = identity.ROOT, legacy: Path | None = None,
                  config: Path = CONFIG, *, allow_legacy=False):
    from .operations import CallableRunner, OperationResult, StepResult, _firmware_supports_secure_boot
    result = OperationResult('sync-defaults')
    legacy = legacy or identity.LEGACY_KEY.parent
    name = identity.current_identity(root)
    if not name and not allow_legacy:
        result.steps['defaults'] = StepResult('skipped', 'No managed signing identity')
        return result
    if not _firmware_supports_secure_boot(result, run):
        return result
    state = identity.inspect_identity(CallableRunner(run), root)
    if not name and not state.usable:
        result.steps['defaults'] = StepResult('skipped', 'No enrolled unified signing identity')
        return result
    if state.enrollment in {'pending', 'absent'}:
        result.steps['defaults'] = StepResult('skipped', 'Enrollment is not complete; previous signing defaults retained')
        return result
    if not state.usable:
        raise ValueError('Cannot verify an enrolled unified identity; previous signing defaults retained'
                         + (': ' + state.detail if state.detail else ''))
    key, cert = (identity.identity_paths(root, name) if name else
                 (identity.LEGACY_KEY, identity.LEGACY_CERTIFICATE))
    for path in (root, key, cert, legacy / 'MOK.priv', legacy / 'MOK.der', config):
        safe_path(path)
    identity.check_pair(key, cert, run)
    # Refuse arbitrary shell configuration rather than replacing user hooks.
    from .operations import CONFIG_CONTENT
    previous_config = config.read_text() if config.exists() else ''
    managed_config = re.fullmatch(
        rf'mok_signing_key="{re.escape(str(root))}/identities/([0-9a-f]{{32}})/MOK.priv"\n'
        rf'mok_certificate="{re.escape(str(root))}/identities/\1/MOK.der"\n', previous_config)
    if previous_config and previous_config not in {CONFIG_CONTENT, configuration(key, cert)} and not managed_config:
        raise ValueError(f'Custom DKMS signing configuration requires review: {config}')
    # DKMS sources other files too. Do not silently claim success if they
    # supply a competing key/certificate or an alternate signing command.
    obsolete = obsolete_oobe_config(config)
    conflict = configuration_conflict(config, ignored=obsolete)
    if conflict:
        raise ValueError(f'Additional DKMS signing override requires review: {conflict}')
    new = [(config, configuration(key, cert).encode(), 0o644),
           (legacy / 'MOK.priv', key.read_bytes(), 0o600),
           (legacy / 'MOK.der', cert.read_bytes(), 0o644)]
    if obsolete:
        # Back up and remove this later-loaded override before changing the
        # default pair. Rollback restores it last, after the original pair.
        new.insert(0, (obsolete, None, 0o600))
    if all(path.is_file() and path.read_bytes() == data and path.stat().st_mode & 0o777 == mode
           for path, data, mode in new):
        result.steps['defaults'] = StepResult('skipped', 'Enrolled certificate is already the signing default')
        return result
    backup_root = root / 'backups'
    safe_path(backup_root)
    root.mkdir(mode=0o755, exist_ok=True)
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup_root.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix='signing-', dir=backup_root))
    saved = []
    for index, (path, _, _) in enumerate(new):
        data = path.read_bytes() if path.exists() else None
        mode = path.stat().st_mode & 0o777 if data is not None else 0o600
        saved.append((path, data, mode))
        if data is not None:
            atomic_write(backup / f'{index}-{path.name}', data, 0o600)
    atomic_write(backup / 'README', (
        'Original signing files before automatic enrollment synchronization.\n'
        + '\n'.join(f'{index}: {path} ({"present" if data is not None else "absent"})'
                    for index, (path, data, _) in enumerate(saved)) + '\n').encode(), 0o600)
    for directory in (backup_root, root):
        descriptor = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    try:
        for path, data, mode in new:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if data is None:
                path.unlink()
                descriptor = os.open(path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            else:
                atomic_write(path, data, mode)
        identity.check_pair(legacy / 'MOK.priv', legacy / 'MOK.der', run)
    except Exception:
        # Restore compatibility copies before pointing DKMS back at them.
        for path, data, mode in reversed(saved):
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data, mode)
        raise
    result.steps['defaults'] = StepResult('success',
        f'Future signing uses the enrolled certificate. Old files backed up at {backup}. '
        'Existing modules, enrolled certificates and the bootloader were not changed.')
    return result
