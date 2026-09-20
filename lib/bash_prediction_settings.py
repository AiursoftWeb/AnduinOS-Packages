"""Shared per-user prediction settings for Control Panel and OOBE.

Packaged privately by both frontends; the file format is also read by the Bash
loader. Never execute shell configuration to discover legacy preferences.
"""
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

DEFAULTS = {"enabled": True, "history": True, "persist": True}
VARIABLES = {"enabled": "ANDUINOS_GUESS_COMMAND", "history": "ANDUINOS_GUESS_HISTORY", "persist": "ANDUINOS_GUESS_PERSIST"}


def _home(home=None):
    return Path(home) if home is not None else Path.home()


def _xdg(name, fallback, home=None):
    value = os.environ.get(name, "") if home is None else ""
    return Path(value) if value.startswith("/") else _home(home) / fallback


def config_path(home=None):
    return _xdg("XDG_CONFIG_HOME", ".config", home) / "anduinos-bash-guess-command/settings.conf"


def state_directory(home=None):
    return _xdg("XDG_STATE_HOME", ".local/state", home) / "anduinos-bash-guess-command"


def _read_regular(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return ""
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise OSError("Invalid settings file")
        return stream.read(limit).decode("utf-8", errors="replace")


def read_settings(home=None, bashrc_path=None):
    result = DEFAULTS.copy()
    # Environment and literal legacy assignments are fallback values only.
    for key, variable in VARIABLES.items():
        if variable in os.environ:
            result[key] = os.environ[variable] == "1" if key == "persist" else os.environ[variable] != "0"
    bashrc = Path(bashrc_path) if bashrc_path else _home(home) / ".bashrc"
    # Existing dotfile symlinks are supported without executing their contents.
    contents = _read_regular(bashrc.resolve(), 4 * 1024 * 1024)
    for key, variable in VARIABLES.items():
        pattern = rf"(?m)^\s*(?:export\s+)?{variable}\s*=\s*(['\"]?)([01])\1\s*(?:#.*)?$"
        values = list(re.finditer(pattern, contents))
        if values:
            result[key] = values[-1][2] == "1"
    for line in _read_regular(config_path(home), 4096).splitlines()[:32]:
        key, sep, value = line.partition("=")
        if sep and key in result and value in {"0", "1"}:
            result[key] = value == "1"
    if not result["history"]:
        result["persist"] = False
    return result


def _private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise OSError("Invalid settings directory")
    path.chmod(0o700)


def _atomic_write(path, contents):
    _private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_settings(settings, home=None):
    if set(settings) != set(DEFAULTS) or any(type(v) is not bool for v in settings.values()):
        raise ValueError("Invalid prediction settings")
    values = {**settings, "persist": settings["history"] and settings["persist"]}
    _atomic_write(config_path(home), "".join(f"{key}={int(values[key])}\n" for key in DEFAULTS))


def set_enabled(enabled, home=None, bashrc_path=None):
    settings = read_settings(home, bashrc_path)
    changed = settings["enabled"] != enabled
    settings["enabled"] = bool(enabled)
    save_settings(settings, home)
    return changed


def restore_defaults(home=None):
    # Write explicit defaults so old shell overrides do not silently reappear.
    save_settings(DEFAULTS, home)


def clear_learning(home=None):
    directory = state_directory(home)
    _private_directory(directory)
    with ExitStack() as stack:
        # Same ordering and lock names as the Rust writer; never unlink locks.
        for name in ("history-v1.lock", "transitions-v1.lock"):
            fd = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            stream = stack.enter_context(os.fdopen(fd, "r+b"))
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("Invalid learning lock")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        # Writers capture this generation when starting. Pending and future
        # writes from older engines are rejected while holding their file lock.
        _atomic_write(directory / "generation", secrets.token_hex(16) + "\n")
        for name in ("history-v1", "transitions-v1"):
            (directory / name).unlink(missing_ok=True)
