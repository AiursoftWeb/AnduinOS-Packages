"""Centralized, structured logging for the AnduinOS Help Center.

Logs are written to both stderr (human-readable) and a rotating log file
under ``XDG_STATE_HOME/anduinos-help/``. Sensitive information must never
be passed to these helpers — see ``safe_for_log`` for a quick sanitiser.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_SENSITIVE_KEYS = {
    "password", "passwd", "secret", "token", "api_key",
    "apikey", "access_token", "refresh_token", "cookie", "authorization",
}


def safe_for_log(value: Any) -> Any:
    """Return a copy of *value* with sensitive fields masked.

    Accepts dicts and shallow lists. Anything else is returned untouched.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = "***"
            else:
                out[k] = safe_for_log(v)
        return out
    if isinstance(value, list):
        return [safe_for_log(v) for v in value]
    return value


def _log_dir() -> Path:
    """Return the writable log directory, honouring XDG_STATE_HOME."""
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        root = Path(base) / "anduinos-help"
    else:
        root = Path.home() / ".local" / "state" / "anduinos-help"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_logger(name: str = "anduinos-help") -> logging.Logger:
    """Return a configured logger. Safe to call repeatedly."""
    logger = logging.getLogger(name)
    if getattr(logger, "_anduinos_configured", False):
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    logger.addHandler(stream)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            _log_dir() / "help.log",
            maxBytes=512 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        logger.addHandler(file_handler)
    except OSError:
        # Read-only filesystems (e.g. system-wide install) — fall back to stderr.
        pass

    logger._anduinos_configured = True  # type: ignore[attr-defined]
    return logger


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> None:
    """Log an exception with traceback at DEBUG, message at ERROR."""
    logger.error("%s: %s", message, exc)
    logger.debug("Traceback:", exc_info=exc)
